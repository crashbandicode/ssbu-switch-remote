# ESP-IDF project (v5.5.6, target esp32s3): idf.py set-target esp32s3 && idf.py build
# Built on the Yoga by tools/build_pad.ps1. The host builds of the core use firmware/Makefile.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(switch_pad)
