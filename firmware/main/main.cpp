// switch-pad firmware for the Freenove ESP32-S3 WROOM board (DESIGN.md section 13, docs/GC-MODE.md).
//   OTG port (native USB, GPIO19/20)  -> the Switch dock, one of two personalities (Pro at power-up):
//       Pro: a HORI POKKEN CONTROLLER (0F0D:0092) wired pad
//       GC:  a GameCube Controller Adapter (057E:0337), our pad on port 1
//   UART port (CH343 -> UART0 43/44)  -> the host PC: switch-remote's line protocol at 921,600 baud
// The logic is swpad::Pad (firmware/core, host-tested); this file is only the ESP-IDF glue. `MODE gc|pro` makes the
// core flag a change; the USB task then soft-detaches, swaps the descriptors in place and re-attaches.
#include <atomic>
#include <cstring>

#include "class/hid/hid_device.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "tinyusb.h"

#include "pad.hpp"

namespace {

constexpr uart_port_t kUart = UART_NUM_0;
constexpr int kBaud = 921600;
constexpr int kDetachMs = 500;  // long enough for the console to see an unplug

// --- Pro mode: a HORI POKKEN CONTROLLER with the 16-button report (HOME and CAPTURE usable).
// Bytes from GP2040-CE headers/drivers/switch/SwitchDescriptors.h @0195793 (MIT, (c) 2021 Jason Skuby);
// see firmware/THIRD_PARTY.md. ---
const tusb_desc_device_t kProDevice = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = 64,
    .idVendor = 0x0F0D,
    .idProduct = 0x0092,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x00,
    .bNumConfigurations = 0x01,
};

const uint8_t kProReport[] = {
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0x15, 0x00, 0x25, 0x01, 0x35, 0x00, 0x45, 0x01, 0x75, 0x01, 0x95, 0x10,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x81, 0x02, 0x05, 0x01, 0x25, 0x07, 0x46, 0x3B, 0x01, 0x75, 0x04, 0x95,
    0x01, 0x65, 0x14, 0x09, 0x39, 0x81, 0x42, 0x65, 0x00, 0x95, 0x01, 0x81, 0x01, 0x26, 0xFF, 0x00, 0x46, 0xFF,
    0x00, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02, 0x06, 0x00, 0xFF,
    0x09, 0x20, 0x95, 0x01, 0x81, 0x02, 0x0A, 0x21, 0x26, 0x95, 0x08, 0x91, 0x02, 0xC0,
};
static_assert(sizeof(kProReport) == 86, "HORI report descriptor is 86 bytes");

const uint8_t kProConfig[] = {
    0x09, 0x02, 0x29, 0x00, 0x01, 0x01, 0x00, 0x80, 0xFA,  // configuration: 41 bytes, 1 interface, 500 mA
    0x09, 0x04, 0x00, 0x00, 0x02, 0x03, 0x00, 0x00, 0x00,  // interface 0: HID, 2 endpoints
    0x09, 0x21, 0x11, 0x01, 0x00, 0x01, 0x22, 0x56, 0x00,  // HID 1.11, report descriptor 86 bytes
    0x07, 0x05, 0x02, 0x03, 0x40, 0x00, 0x01,              // EP 0x02 OUT interrupt, 64 B, 1 ms
    0x07, 0x05, 0x81, 0x03, 0x40, 0x00, 0x01,              // EP 0x81 IN interrupt, 64 B, 1 ms
};
static_assert(sizeof(kProConfig) == 41, "configuration descriptor is 41 bytes");

// --- GC mode: a GameCube Controller Adapter (docs/GC-MODE.md for the public sources). The report descriptor is our
// own: it declares exactly the adapter's reports (output 0x11 rumble 4 B, output 0x13 init, input 0x21 36 B). ---
const tusb_desc_device_t kGcDevice = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = 64,
    .idVendor = 0x057E,
    .idProduct = 0x0337,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x00,
    .bNumConfigurations = 0x01,
};

#define GC_BYTES(id, count, main) \
    0x85, id, 0x19, 0x00, 0x2A, 0xFF, 0x00, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, count, main, 0x00
const uint8_t kGcReport[] = {
    0x05, 0x05,                       // Usage Page (Game Controls)
    0x09, 0x00,                       // Usage (Undefined)
    0xA1, 0x01,                       // Collection (Application)
    GC_BYTES(0x11, 0x04, 0x91),       //   Report 0x11: 4 bytes Output (rumble, one byte per port)
    GC_BYTES(0x21, 0x24, 0x81),       //   Report 0x21: 36 bytes Input (four 9-byte ports)
    GC_BYTES(0x13, 0x01, 0x91),       //   Report 0x13: 1 byte Output (init)
    0xC0,                             // End Collection
};
#undef GC_BYTES
static_assert(sizeof(kGcReport) == 61, "GC report descriptor is 61 bytes");

const uint8_t kGcConfig[] = {
    0x09, 0x02, 0x29, 0x00, 0x01, 0x01, 0x00, 0x80, 0xFA,  // configuration: 41 bytes, 1 interface, bus powered, 500 mA
    0x09, 0x04, 0x00, 0x00, 0x02, 0x03, 0x00, 0x00, 0x00,  // interface 0: HID, 2 endpoints
    0x09, 0x21, 0x10, 0x01, 0x00, 0x01, 0x22, sizeof(kGcReport), 0x00,  // HID 1.10, our report descriptor
    0x07, 0x05, 0x81, 0x03, 0x25, 0x00, 0x08,              // EP 0x81 IN interrupt, 37 B, 8 ms (stock adapter)
    0x07, 0x05, 0x02, 0x03, 0x05, 0x00, 0x08,              // EP 0x02 OUT interrupt, 5 B, 8 ms
};
static_assert(sizeof(kGcConfig) == 41, "GC configuration descriptor is 41 bytes");

// esp_tinyusb 1.7.6 keeps the device and configuration pointers it is given and copies only the string POINTER array,
// so the live descriptors are these buffers, rewritten in place while the device is detached.
tusb_desc_device_t g_device;
uint8_t g_config[41];
char g_manufacturer[24], g_product[24];
const char kLang[] = {0x09, 0x04};
const char* g_strings[] = {kLang, g_manufacturer, g_product, "1.0"};

swpad::Pad g_pad;  // ~78 KB (the macro buffer and its trigger bytes), static
SemaphoreHandle_t g_lock;
std::atomic<bool> g_enumerated{true};  // false from a mode switch until the console's next SET_CONFIGURATION

void present(swpad::Mode m) {
    const bool gc = m == swpad::Mode::Gc;
    g_device = gc ? kGcDevice : kProDevice;
    std::memcpy(g_config, gc ? kGcConfig : kProConfig, sizeof g_config);
    std::strncpy(g_manufacturer, gc ? "Nintendo" : "HORI CO.,LTD.", sizeof g_manufacturer - 1);
    std::strncpy(g_product, gc ? "WUP-028" : "POKKEN CONTROLLER", sizeof g_product - 1);
}

void uart_task(void*) {
    static char line[512];
    size_t n = 0;
    uint8_t buf[256];
    sr::LineBuf<192> out;
    for (;;) {
        int got = uart_read_bytes(kUart, buf, sizeof buf, pdMS_TO_TICKS(20));
        for (int i = 0; i < got; i++) {
            if (buf[i] != '\n') {
                if (n < sizeof line) line[n++] = char(buf[i]);
                continue;
            }
            xSemaphoreTake(g_lock, portMAX_DELAY);
            g_pad.handle(std::string_view(line, n), uint64_t(esp_timer_get_time()), out);
            xSemaphoreGive(g_lock);
            n = 0;
            if (out.len) {
                uart_write_bytes(kUart, out.buf, out.len);
                uart_write_bytes(kUart, "\n", 1);
            }
        }
    }
}

// Detach, swap, re-attach. Runs on the USB task, so no report is in flight from us meanwhile.
void switch_mode(swpad::Mode next) {
    g_enumerated = false;
    tud_disconnect();
    vTaskDelay(pdMS_TO_TICKS(kDetachMs));
    xSemaphoreTake(g_lock, portMAX_DELAY);
    present(next);
    xSemaphoreGive(g_lock);
    tud_connect();
}

// Refreshes the HID report every millisecond: macro frames change on the device clock, the console reads the
// newest report at its own poll.
void usb_task(void*) {
    for (;;) {
        swpad::Mode next;
        xSemaphoreTake(g_lock, portMAX_DELAY);
        bool change = g_pad.take_mode_change(next);
        xSemaphoreGive(g_lock);
        if (change) switch_mode(next);

        const bool live = g_enumerated && tud_mounted();
        xSemaphoreTake(g_lock, portMAX_DELAY);
        g_pad.set_usb_mounted(live);
        const uint64_t now = uint64_t(esp_timer_get_time());
        const swpad::Mode mode = g_pad.mode();
        swpad::PadOut s = g_pad.state(now);
        xSemaphoreGive(g_lock);
        bool sent = false;
        if (live && tud_hid_ready()) {
            if (mode == swpad::Mode::Gc) {
                swpad::GcReport r = swpad::make_gc_report(s);
                sent = tud_hid_report(swpad::kGcReportId, r.b + 1, sizeof r.b - 1);  // TinyUSB prepends the ID
            } else {
                swpad::Report r = swpad::Pad::to_report(s);
                sent = tud_hid_report(0, r.b, sizeof r.b);
            }
        }
        if (sent) {
            xSemaphoreTake(g_lock, portMAX_DELAY);
            g_pad.count_report();
            xSemaphoreGive(g_lock);
        }
        vTaskDelay(1);  // CONFIG_FREERTOS_HZ=1000
    }
}

}  // namespace

// The console configured the device: after a mode switch this is the new personality's enumeration.
extern "C" void tud_mount_cb(void) { g_enumerated = true; }

extern "C" uint8_t const* tud_hid_descriptor_report_cb(uint8_t) {
    return g_device.idVendor == kGcDevice.idVendor ? kGcReport : kProReport;
}

extern "C" uint16_t tud_hid_get_report_cb(uint8_t, uint8_t, hid_report_type_t, uint8_t*, uint16_t) { return 0; }

// Output reports: in GC mode the adapter's 0x13 init and 0x11 rumble (counted / recorded by the core); in Pro mode
// (rumble/LED on the Pro Controller path) they are ignored.
extern "C" void tud_hid_set_report_cb(uint8_t, uint8_t report_id, hid_report_type_t, uint8_t const* buffer,
                                      uint16_t bufsize) {
    xSemaphoreTake(g_lock, portMAX_DELAY);
    g_pad.output_report(report_id, buffer, bufsize);
    xSemaphoreGive(g_lock);
}

extern "C" void app_main(void) {
    g_lock = xSemaphoreCreateMutex();
    present(swpad::Mode::Pro);  // always Pro at power-up: the mode is never stored
    uart_config_t uc = {};
    uc.baud_rate = kBaud;
    uc.data_bits = UART_DATA_8_BITS;
    uc.parity = UART_PARITY_DISABLE;
    uc.stop_bits = UART_STOP_BITS_1;
    uc.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    uc.source_clk = UART_SCLK_DEFAULT;
    ESP_ERROR_CHECK(uart_driver_install(kUart, 16384, 4096, 0, nullptr, 0));
    ESP_ERROR_CHECK(uart_param_config(kUart, &uc));
    // With CONFIG_ESP_CONSOLE_NONE nothing routes UART0 to its pins: the board's CH343 is on TX GPIO43 / RX GPIO44
    // (Freenove C_Tutorial.pdf p.145). Hardware test 2026-11-16: without this the app never answered.
    ESP_ERROR_CHECK(uart_set_pin(kUart, 43, 44, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    tinyusb_config_t tc = {};
    tc.device_descriptor = &g_device;
    tc.string_descriptor = g_strings;
    tc.string_descriptor_count = sizeof g_strings / sizeof g_strings[0];
    tc.external_phy = false;
    tc.configuration_descriptor = g_config;
    ESP_ERROR_CHECK(tinyusb_driver_install(&tc));

    xTaskCreatePinnedToCore(usb_task, "pad_usb", 4096, nullptr, 6, nullptr, 1);
    xTaskCreatePinnedToCore(uart_task, "pad_uart", 4096, nullptr, 5, nullptr, 0);
}
