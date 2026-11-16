idf_component_register(
    SRCS "main.cpp" "../core/pad.cpp" "../../sysmodule/core/core.cpp"
    INCLUDE_DIRS "." "../core" "../../sysmodule/core"
    PRIV_REQUIRES esp_driver_uart esp_timer)
target_compile_options(${COMPONENT_LIB} PRIVATE -std=gnu++17 -Wall -Wextra -Werror -Wno-missing-field-initializers)
