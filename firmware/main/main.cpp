// switch-pad firmware for the Freenove ESP32-S3 WROOM board (DESIGN.md section 13).
//   OTG port (native USB, GPIO19/20)  -> the Switch dock: a HORI POKKEN CONTROLLER (0F0D:0092) wired pad
//   UART port (CH343 -> UART0 43/44)  -> the host PC: switch-remote's line protocol at 921,600 baud
// The logic is swpad::Pad (firmware/core, host-tested); this file is only the ESP-IDF glue.
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

// --- USB descriptors: a HORI POKKEN CONTROLLER with the 16-button report (HOME and CAPTURE usable).
// Bytes from GP2040-CE headers/drivers/switch/SwitchDescriptors.h @0195793 (MIT, (c) 2021 Jason Skuby);
// see firmware/THIRD_PARTY.md. ---
const tusb_desc_device_t kDevice = {
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

const uint8_t kReport[] = {
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0x15, 0x00, 0x25, 0x01, 0x35, 0x00, 0x45, 0x01, 0x75, 0x01, 0x95, 0x10,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x81, 0x02, 0x05, 0x01, 0x25, 0x07, 0x46, 0x3B, 0x01, 0x75, 0x04, 0x95,
    0x01, 0x65, 0x14, 0x09, 0x39, 0x81, 0x42, 0x65, 0x00, 0x95, 0x01, 0x81, 0x01, 0x26, 0xFF, 0x00, 0x46, 0xFF,
    0x00, 0x09, 0x30, 0x09, 0x31, 0x09, 0x32, 0x09, 0x35, 0x75, 0x08, 0x95, 0x04, 0x81, 0x02, 0x06, 0x00, 0xFF,
    0x09, 0x20, 0x95, 0x01, 0x81, 0x02, 0x0A, 0x21, 0x26, 0x95, 0x08, 0x91, 0x02, 0xC0,
};
static_assert(sizeof(kReport) == 86, "HORI report descriptor is 86 bytes");

const uint8_t kConfig[] = {
    0x09, 0x02, 0x29, 0x00, 0x01, 0x01, 0x00, 0x80, 0xFA,  // configuration: 41 bytes, 1 interface, 500 mA
    0x09, 0x04, 0x00, 0x00, 0x02, 0x03, 0x00, 0x00, 0x00,  // interface 0: HID, 2 endpoints
    0x09, 0x21, 0x11, 0x01, 0x00, 0x01, 0x22, 0x56, 0x00,  // HID 1.11, report descriptor 86 bytes
    0x07, 0x05, 0x02, 0x03, 0x40, 0x00, 0x01,              // EP 0x02 OUT interrupt, 64 B, 1 ms
    0x07, 0x05, 0x81, 0x03, 0x40, 0x00, 0x01,              // EP 0x81 IN interrupt, 64 B, 1 ms
};
static_assert(sizeof(kConfig) == 41, "configuration descriptor is 41 bytes");

const char kLang[] = {0x09, 0x04};
const char* kStrings[] = {kLang, "HORI CO.,LTD.", "POKKEN CONTROLLER", "1.0"};

swpad::Pad g_pad;  // ~70 KB (the macro buffer), static
SemaphoreHandle_t g_lock;

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

// Refreshes the HID report every millisecond: macro frames change on the device clock, the console reads the
// newest report at its own poll.
void usb_task(void*) {
    for (;;) {
        xSemaphoreTake(g_lock, portMAX_DELAY);
        g_pad.set_usb_mounted(tud_mounted());
        swpad::Report r = g_pad.report(uint64_t(esp_timer_get_time()));
        xSemaphoreGive(g_lock);
        if (tud_mounted() && tud_hid_ready() && tud_hid_report(0, r.b, sizeof r.b)) {
            xSemaphoreTake(g_lock, portMAX_DELAY);
            g_pad.count_report();
            xSemaphoreGive(g_lock);
        }
        vTaskDelay(1);  // CONFIG_FREERTOS_HZ=1000
    }
}

}  // namespace

extern "C" uint8_t const* tud_hid_descriptor_report_cb(uint8_t) { return kReport; }

extern "C" uint16_t tud_hid_get_report_cb(uint8_t, uint8_t, hid_report_type_t, uint8_t*, uint16_t) { return 0; }

// The console's output reports (rumble/LED on the Pro Controller path) are ignored.
extern "C" void tud_hid_set_report_cb(uint8_t, uint8_t, hid_report_type_t, uint8_t const*, uint16_t) {}

extern "C" void app_main(void) {
    g_lock = xSemaphoreCreateMutex();
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
    tc.device_descriptor = &kDevice;
    tc.string_descriptor = kStrings;
    tc.string_descriptor_count = sizeof kStrings / sizeof kStrings[0];
    tc.external_phy = false;
    tc.configuration_descriptor = kConfig;
    ESP_ERROR_CHECK(tinyusb_driver_install(&tc));

    xTaskCreatePinnedToCore(usb_task, "pad_usb", 4096, nullptr, 6, nullptr, 1);
    xTaskCreatePinnedToCore(uart_task, "pad_uart", 4096, nullptr, 5, nullptr, 0);
}
