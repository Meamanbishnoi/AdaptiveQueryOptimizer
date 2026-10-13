@echo off
echo [BUILD] Compiling C++ native telemetry probe with g++ -O3...
if not exist "bin" mkdir "bin"

g++ -O3 -march=native -std=c++17 -Wall -Wextra src\native\telemetry_probe.cpp -o bin\os_telemetry_probe.exe

if %ERRORLEVEL% equ 0 (
    echo [BUILD] Success! Binary built at bin\os_telemetry_probe.exe
) else (
    echo [BUILD] Compilation failed with error code %ERRORLEVEL%
)
