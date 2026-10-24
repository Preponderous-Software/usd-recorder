# The usd-replay/1 reader: a static library and the replay-dump tool the conformance test uses.
find_package(pxr CONFIG REQUIRED)

function(usd_recorder_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /permissive-)
    if(USD_RECORDER_WARNINGS_AS_ERRORS)
      target_compile_options(${target} PRIVATE /WX)
    endif()
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
    if(USD_RECORDER_WARNINGS_AS_ERRORS)
      target_compile_options(${target} PRIVATE -Werror)
    endif()
  endif()
endfunction()

add_library(usd_recorder_replay_reader STATIC src/replay_reader.cpp)
target_include_directories(usd_recorder_replay_reader PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
# SYSTEM: OpenUSD's own headers are not held to these warning flags.
target_include_directories(usd_recorder_replay_reader SYSTEM PRIVATE ${PXR_INCLUDE_DIRS})
target_link_libraries(usd_recorder_replay_reader PUBLIC usd usdGeom sdf tf vt gf)
target_compile_features(usd_recorder_replay_reader PUBLIC cxx_std_17)
# OpenUSD's tf headers pull in libstdc++'s <ext/hash_*> "backward" headers, whose #warning
# -isystem does not silence.
target_compile_options(usd_recorder_replay_reader PRIVATE $<$<CXX_COMPILER_ID:GNU>:-Wno-deprecated>)
usd_recorder_warnings(usd_recorder_replay_reader)
add_library(usd_recorder::replay_reader ALIAS usd_recorder_replay_reader)

add_executable(replay-dump tools/replay_dump.cpp)
target_link_libraries(replay-dump PRIVATE usd_recorder_replay_reader)
usd_recorder_warnings(replay-dump)
