cmake_minimum_required(VERSION 3.24)
project(ogc_native LANGUAGES CXX)

# The build caller must unpack the pinned, vendored pybind11 source first.  This
# deliberately avoids network-backed FetchContent during competition builds.
if(NOT DEFINED PYBIND11_SOURCE_DIR)
  message(FATAL_ERROR "Set PYBIND11_SOURCE_DIR to the unpacked pinned pybind11 source")
endif()
if(EXISTS "${PYBIND11_SOURCE_DIR}/CMakeLists.txt")
  add_subdirectory("${PYBIND11_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/pybind11")
elseif(EXISTS "${PYBIND11_SOURCE_DIR}/pybind11/share/cmake/pybind11/pybind11Config.cmake")
  set(pybind11_DIR "${PYBIND11_SOURCE_DIR}/pybind11/share/cmake/pybind11")
  find_package(pybind11 CONFIG REQUIRED)
else()
  message(FATAL_ERROR "Pinned pybind11 source has no supported CMake package")
endif()
find_package(GEOS CONFIG REQUIRED)

pybind11_add_module(_ogc_native MODULE
  src/bindings.cpp
  src/exact_geometry.cpp
  src/repair_kernel.cpp
)
target_include_directories(_ogc_native PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(_ogc_native PRIVATE GEOS::geos_c)
target_compile_features(_ogc_native PRIVATE cxx_std_20)
set_target_properties(_ogc_native PROPERTIES
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN ON
)

if(MSVC)
  target_compile_options(_ogc_native PRIVATE /O2 /DNDEBUG)
else()
  target_compile_options(_ogc_native PRIVATE -O3 -DNDEBUG -fvisibility=hidden)
endif()

# Numerical candidate logic is intentionally not present in P2.  Keep these
# checks explicit so a later kernel cannot quietly opt into unsafe flags.
string(CONCAT _ogc_native_flags "${CMAKE_CXX_FLAGS} ${CMAKE_CXX_FLAGS_RELEASE}")
if(_ogc_native_flags MATCHES "(^| )-ffast-math( |$)" OR
   _ogc_native_flags MATCHES "(^| )-march=native( |$)")
  message(FATAL_ERROR "-ffast-math and -march=native are forbidden for ogc_native")
endif()
