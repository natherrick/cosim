# examples/MyModule/CMakeLists.txt
#
# This file is part of NEST.
#
# Copyright (C) 2004 The NEST Initiative
#
# NEST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# NEST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with NEST.  If not, see <http://www.gnu.org/licenses/>.

cmake_minimum_required( VERSION 2.8.12 )

# This CMakeLists.txt is configured to build your external module for NEST. For
# illustrative reasons this module is called 'my' (change SHORT_NAME to your
# preferred module name). NEST requires you to extend the 'SLIModule' (see
# mymodule.h and mymodule.cpp as an example) and provide a module header
# (see MODULE_HEADER). The subsequent instructions
#
# The configuration requires a compiled and installed NEST; if `nest-config` is
# not in the PATH, please specify the absolute path with `-Dwith-nest=...`.
#
# For more informations on how to extend and use your module see:
#           https://nest.github.io/nest-simulator/extension_modules

# 1) Name your module here, i.e. add later with -Dexternal-modules=my:
set( SHORT_NAME ${moduleName} )

#    the complete module name is here:
set( MODULE_NAME ${r"$"}{SHORT_NAME}module )

# 2) Add all your sources here
set( MODULE_SOURCES
    ${moduleName}.h ${moduleName}.cpp
    <#list neurons as neuron>
      ${neuron.getName()}.cpp ${neuron.getName()}.h <#if neuron_has_next>\</#if>
    </#list>
    )

# 3) We require a header name like this:
set( MODULE_HEADER ${r"$"}{MODULE_NAME}.h )
# containing the class description of the class extending the SLIModule

# 4) Specify your module version
set( MODULE_VERSION_MAJOR 1 )
set( MODULE_VERSION_MINOR 0 )
set( MODULE_VERSION "${r"$"}{MODULE_VERSION_MAJOR}.${r"$"}{MODULE_VERSION_MINOR}" )

# 5) Leave the rest as is. All files in `sli` will be installed to
#    `share/nest/sli/`, so that NEST will find the during initialization.

# Leave the call to "project(...)" for after the compiler is determined.

# Set the `nest-config` executable to use during configuration.
set( with-nest OFF CACHE STRING "Specify the `nest-config` executable." )

# If it is not set, look for a `nest-config` in the PATH.
if ( NOT with-nest )
  # try find the program ourselves
  find_program( NEST_CONFIG
      NAMES nest-config
      )
  if ( NEST_CONFIG STREQUAL "NEST_CONFIG-NOTFOUND" )
    message( FATAL_ERROR "Cannot find the program `nest-config`. Specify via -Dwith-nest=... ." )
  endif ()
else ()
  set( NEST_CONFIG ${r"$"}{with-nest} )
endif ()

# Use `nest-config` to get the compile and installation options used with the
# NEST installation.

# Get the compiler that was used for NEST.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --compiler
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_COMPILER
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# One check on first execution, if `nest-config` is working.
if ( NOT RES_VAR EQUAL 0 )
  message( FATAL_ERROR "Cannot run `${r"$"}{NEST_CONFIG}`. Please specify correct `nest-config` via -Dwith-nest=... " )
endif ()

# Setting the compiler has to happen before the call to "project(...)" function.
set( CMAKE_CXX_COMPILER "${r"$"}{NEST_COMPILER}" )

project( ${r"$"}{MODULE_NAME} CXX )

# Get the install prefix.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --prefix
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_PREFIX
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# Use the `NEST_PREFIX` as `CMAKE_INSTALL_PREFIX`.
set( CMAKE_INSTALL_PREFIX "${r"$"}{NEST_PREFIX}" CACHE STRING "Install path prefix, prepended onto install directories." FORCE )

# Get the CXXFLAGS.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --cflags
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_CXXFLAGS
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# Get the Includes.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --includes
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_INCLUDES
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if ( NEST_INCLUDES )
  # make a cmake list
  string( REPLACE " " ";" NEST_INCLUDES_LIST "${r"$"}{NEST_INCLUDES}" )
  foreach ( inc_complete ${r"$"}{NEST_INCLUDES_LIST} )
    # if it is actually a -Iincludedir
    if ( "${r"$"}{inc_complete}" MATCHES "^-I.*" )
      # get the directory
      string( REGEX REPLACE "^-I(.*)" "\\1" inc "${r"$"}{inc_complete}" )
      # and check whether it is a directory
      if ( IS_DIRECTORY "${r"$"}{inc}" )
        include_directories( "${r"$"}{inc}" )
      endif ()
    endif ()
  endforeach ()
endif ()

# Get, if NEST is build as a (mostly) static application. If yes, also only build
# static library.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --static-libraries
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_STATIC_LIB
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
if ( NEST_STATIC_LIB )
  set( BUILD_SHARED_LIBS OFF )
else ()
  set( BUILD_SHARED_LIBS ON )
endif ()

# Get all linked libraries.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --libs
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_LIBS
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# Get the data install dir.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --datadir
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_DATADIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# Get the documentation install dir.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --docdir
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_DOCDIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# Get the library install dir.
execute_process(
    COMMAND ${r"$"}{NEST_CONFIG} --libdir
    RESULT_VARIABLE RES_VAR
    OUTPUT_VARIABLE NEST_LIBDIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

# on OS X
set( CMAKE_MACOSX_RPATH ON )

# Install all stuff to NEST's install directories.
set( CMAKE_INSTALL_LIBDIR ${r"$"}{NEST_LIBDIR}/nest CACHE STRING "object code libraries (lib/nest or lib64/nest or lib/<multiarch-tuple>/nest on Debian)" FORCE )
set( CMAKE_INSTALL_DOCDIR ${r"$"}{NEST_DOCDIR} CACHE STRING "documentation root (DATAROOTDIR/doc/nest)" FORCE )
set( CMAKE_INSTALL_DATADIR ${r"$"}{NEST_DATADIR} CACHE STRING "read-only architecture-independent data (DATAROOTDIR/nest)" FORCE )

include( GNUInstallDirs )

# CPack stuff. Required for target `dist`.
set( CPACK_GENERATOR TGZ )
set( CPACK_SOURCE_GENERATOR TGZ )

set( CPACK_PACKAGE_DESCRIPTION_SUMMARY "NEST Module ${r"$"}{MODULE_NAME}" )
set( CPACK_PACKAGE_VENDOR "NEST Initiative (http://www.nest-initiative.org/)" )

set( CPACK_PACKAGE_VERSION_MAJOR ${r"$"}{MODULE_VERSION_MAJOR} )
set( CPACK_PACKAGE_VERSION_MINOR ${r"$"}{MODULE_VERSION_MINOR} )
set( CPACK_PACKAGE_VERSION ${r"$"}{MODULE_VERSION} )

set( CPACK_SOURCE_IGNORE_FILES
    "\\\\.gitignore"
    "\\\\.git/"
    "\\\\.travis\\\\.yml"

    # if we have in source builds
    "/build/"
    "/_CPack_Packages/"
    "CMakeFiles/"
    "cmake_install\\\\.cmake"
    "Makefile.*"
    "CMakeCache\\\\.txt"
    "CPackConfig\\\\.cmake"
    "CPackSourceConfig\\\\.cmake"
    )
set( CPACK_SOURCE_PACKAGE_FILE_NAME ${r"$"}{MODULE_NAME} )

set( CPACK_PACKAGE_INSTALL_DIRECTORY "${r"$"}{MODULE_NAME} ${r"$"}{MODULE_VERSION}" )
include( CPack )

# add make dist target
add_custom_target( dist
    COMMAND ${r"$"}{CMAKE_MAKE_PROGRAM} package_source
    # not sure about this... seems, that it will be removed before dist...
    # DEPENDS doc
    COMMENT "Creating a source distribution from ${r"$"}{MODULE_NAME}..."
    )


if ( BUILD_SHARED_LIBS )
  # When building shared libraries, also create a module for loading at runtime
  # with the `Install` command.
  add_library( ${r"$"}{MODULE_NAME}_module MODULE ${r"$"}{MODULE_SOURCES} )
  set_target_properties( ${r"$"}{MODULE_NAME}_module
      PROPERTIES
      COMPILE_FLAGS "${r"$"}{NEST_CXXFLAGS} -DLTX_MODULE"
      LINK_FLAGS "${r"$"}{NEST_LIBS}"
      PREFIX ""
      OUTPUT_NAME ${r"$"}{MODULE_NAME} )
  install( TARGETS ${r"$"}{MODULE_NAME}_module
      DESTINATION ${r"$"}{CMAKE_INSTALL_LIBDIR}
      )
endif ()

# Build dynamic/static library for standard linking from NEST.
add_library( ${r"$"}{MODULE_NAME}_lib ${r"$"}{MODULE_SOURCES} )
if ( BUILD_SHARED_LIBS )
  # Dynamic libraries are initiated by a `global` variable of the `SLIModule`,
  # which is included, when the flag `LINKED_MODULE` is set.
  target_compile_definitions( ${r"$"}{MODULE_NAME}_lib PRIVATE -DLINKED_MODULE )
endif ()
set_target_properties( ${r"$"}{MODULE_NAME}_lib
    PROPERTIES
    COMPILE_FLAGS "${r"$"}{NEST_CXXFLAGS}"
    LINK_FLAGS "${r"$"}{NEST_LIBS}"
    OUTPUT_NAME ${r"$"}{MODULE_NAME} )

# Install library, header and sli init files.
install( TARGETS ${r"$"}{MODULE_NAME}_lib DESTINATION ${r"$"}{CMAKE_INSTALL_LIBDIR} )
install( FILES ${r"$"}{MODULE_HEADER} DESTINATION ${r"$"}{CMAKE_INSTALL_INCLUDEDIR} )
install( DIRECTORY sli DESTINATION ${r"$"}{CMAKE_INSTALL_DATADIR} )

# Install help.
set( HELPDIRS "${r"$"}{PROJECT_SOURCE_DIR}:${r"$"}{PROJECT_SOURCE_DIR}/sli" )
install( CODE
    "execute_process(COMMAND ${r"$"}{CMAKE_COMMAND}
          -DDOC_DIR='${r"$"}{CMAKE_INSTALL_FULL_DOCDIR}'
          -DDATA_DIR='${r"$"}{CMAKE_INSTALL_FULL_DATADIR}'
          -DHELPDIRS='${r"$"}{HELPDIRS}'
          -DINSTALL_DIR='${r"$"}{CMAKE_INSTALL_PREFIX}'
          -P ${r"$"}{CMAKE_INSTALL_FULL_DOCDIR}/generate_help.cmake
        WORKING_DIRECTORY \"${r"$"}{PROJECT_BINARY_DIR}\"
      )"
    )

message( "" )
message( "-------------------------------------------------------" )
message( "${r"$"}{MODULE_NAME} Configuration Summary" )
message( "-------------------------------------------------------" )
message( "" )
message( "C++ compiler         : ${r"$"}{CMAKE_CXX_COMPILER}" )
message( "Build static libs    : ${r"$"}{NEST_STATIC_LIB}" )
message( "C++ compiler flags   : ${r"$"}{CMAKE_CXX_FLAGS}" )
message( "NEST compiler flags  : ${r"$"}{NEST_CXXFLAGS}" )
message( "NEST include dirs    : ${r"$"}{NEST_INCLUDES}" )
message( "NEST libraries flags : ${r"$"}{NEST_LIBS}" )
message( "" )
message( "-------------------------------------------------------" )
message( "" )
message( "You can build and install ${r"$"}{MODULE_NAME} now, using" )
message( "  make" )
message( "  make install" )
message( "" )
message( "${r"$"}{MODULE_NAME} will be installed to: ${r"$"}{CMAKE_INSTALL_FULL_LIBDIR}" )
message( "" )