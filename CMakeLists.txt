cmake_minimum_required(VERSION 3.16)

add_compile_options(-Wno-missing-field-initializers)

set(BOARD_NAME "atk-dnesp32s3")
set(BOARD_TYPE "atk-dnesp32s3")
set(PROJECT_VER "2.2.6")

set(PROJECT_DIR "${CMAKE_CURRENT_LIST_DIR}/firmware/atk-dnesp32s3-eye-uart/source/xiaozhi-esp32")
set(EXTRA_COMPONENT_DIRS
    "${PROJECT_DIR}/components"
    "${PROJECT_DIR}/managed_components"
    "${PROJECT_DIR}/main"
)

set(SDKCONFIG_DEFAULTS
    "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
idf_build_set_property(MINIMAL_BUILD ON)
project(xiaozhi)
