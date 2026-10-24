# usd-recorder's C++ side.
#
#   usd_recorder::writers        the header-only, dependency-free .usda writers (cpp/include)
#   usd_recorder::replay_reader  the usd-replay/1 reader (cpp/reader), only with
#                                -DUSD_RECORDER_BUILD_READER=ON; it links OpenUSD, found as a CMake
#                                package (-DCMAKE_PREFIX_PATH=<OpenUSD install>)
#
# A producer that only writes can keep compiling the headers directly (-I cpp/include) and never
# needs this file. A consumer can add it with add_subdirectory(cpp) or FetchContent (SOURCE_SUBDIR
# cpp).
cmake_minimum_required(VERSION 3.16)
project(usd_recorder_cpp LANGUAGES CXX)

option(USD_RECORDER_BUILD_READER "Build the C++ usd-replay/1 reader (needs OpenUSD: Sdf, Usd, UsdGeom)" OFF)
option(USD_RECORDER_WARNINGS_AS_ERRORS "Treat compiler warnings as errors in usd-recorder's own sources" OFF)

add_library(usd_recorder_writers INTERFACE)
target_include_directories(usd_recorder_writers INTERFACE $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
target_compile_features(usd_recorder_writers INTERFACE cxx_std_17)
add_library(usd_recorder::writers ALIAS usd_recorder_writers)

if(USD_RECORDER_BUILD_READER)
  add_subdirectory(reader)
endif()
