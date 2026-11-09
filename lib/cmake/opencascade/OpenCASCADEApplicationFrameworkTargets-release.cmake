#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "TKCAF" for configuration "Release"
set_property(TARGET TKCAF APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKCAF PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKCAF.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKCAF.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKCAF )
list(APPEND _cmake_import_check_files_for_TKCAF "${_IMPORT_PREFIX}/lib/libTKCAF.so.7.9.3" )

# Import target "TKCDF" for configuration "Release"
set_property(TARGET TKCDF APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKCDF PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKCDF.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKCDF.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKCDF )
list(APPEND _cmake_import_check_files_for_TKCDF "${_IMPORT_PREFIX}/lib/libTKCDF.so.7.9.3" )

# Import target "TKLCAF" for configuration "Release"
set_property(TARGET TKLCAF APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKLCAF PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKLCAF.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKLCAF.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKLCAF )
list(APPEND _cmake_import_check_files_for_TKLCAF "${_IMPORT_PREFIX}/lib/libTKLCAF.so.7.9.3" )

# Import target "TKVCAF" for configuration "Release"
set_property(TARGET TKVCAF APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKVCAF PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKVCAF.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKVCAF.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKVCAF )
list(APPEND _cmake_import_check_files_for_TKVCAF "${_IMPORT_PREFIX}/lib/libTKVCAF.so.7.9.3" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
