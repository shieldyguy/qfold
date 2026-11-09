#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "TKDESTEP" for configuration "Release"
set_property(TARGET TKDESTEP APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKDESTEP PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKDESTEP.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKDESTEP.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKDESTEP )
list(APPEND _cmake_import_check_files_for_TKDESTEP "${_IMPORT_PREFIX}/lib/libTKDESTEP.so.7.9.3" )

# Import target "TKDE" for configuration "Release"
set_property(TARGET TKDE APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKDE PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKDE.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKDE.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKDE )
list(APPEND _cmake_import_check_files_for_TKDE "${_IMPORT_PREFIX}/lib/libTKDE.so.7.9.3" )

# Import target "TKXSBase" for configuration "Release"
set_property(TARGET TKXSBase APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKXSBase PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKXSBase.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKXSBase.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKXSBase )
list(APPEND _cmake_import_check_files_for_TKXSBase "${_IMPORT_PREFIX}/lib/libTKXSBase.so.7.9.3" )

# Import target "TKXCAF" for configuration "Release"
set_property(TARGET TKXCAF APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKXCAF PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKXCAF.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKXCAF.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKXCAF )
list(APPEND _cmake_import_check_files_for_TKXCAF "${_IMPORT_PREFIX}/lib/libTKXCAF.so.7.9.3" )

# Import target "TKDESTL" for configuration "Release"
set_property(TARGET TKDESTL APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TKDESTL PROPERTIES
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libTKDESTL.so.7.9.3"
  IMPORTED_SONAME_RELEASE "libTKDESTL.so.7.9"
  )

list(APPEND _cmake_import_check_targets TKDESTL )
list(APPEND _cmake_import_check_files_for_TKDESTL "${_IMPORT_PREFIX}/lib/libTKDESTL.so.7.9.3" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
