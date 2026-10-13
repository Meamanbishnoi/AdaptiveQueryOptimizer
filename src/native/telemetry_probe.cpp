#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <iostream>
#include <fstream>
#include <chrono>
#include <thread>
#include <string>
#include <iomanip>

// Helper to calculate CPU usage percentage from FILETIME structs
static ULONGLONG SubtractTimes(const FILETIME& ftA, const FILETIME& ftB) {
    ULARGE_INTEGER a, b;
    a.LowPart = ftA.dwLowDateTime;
    a.HighPart = ftA.dwHighDateTime;
    b.LowPart = ftB.dwLowDateTime;
    b.HighPart = ftB.dwHighDateTime;
    return a.QuadPart - b.QuadPart;
}

class FastTelemetryProbe {
private:
    FILETIME prevIdleTime;
    FILETIME prevKernelTime;
    FILETIME prevUserTime;
    bool initialized = false;

public:
    FastTelemetryProbe() {
        GetSystemTimes(&prevIdleTime, &prevKernelTime, &prevUserTime);
        initialized = true;
    }

    struct Metrics {
        double cpu_percent;
        double mem_percent;
        double mem_total_mb;
        double mem_used_mb;
        double mem_available_mb;
        double sample_duration_us;
    };

    Metrics Sample() {
        auto t0 = std::chrono::high_resolution_clock::now();

        FILETIME idleTime, kernelTime, userTime;
        GetSystemTimes(&idleTime, &kernelTime, &userTime);

        ULONGLONG idle = SubtractTimes(idleTime, prevIdleTime);
        ULONGLONG kernel = SubtractTimes(kernelTime, prevKernelTime);
        ULONGLONG user = SubtractTimes(userTime, prevUserTime);

        prevIdleTime = idleTime;
        prevKernelTime = kernelTime;
        prevUserTime = userTime;

        ULONGLONG total = kernel + user;
        double cpu = 0.0;
        if (total > 0) {
            ULONGLONG active = (total > idle) ? (total - idle) : 0;
            cpu = (static_cast<double>(active) / static_cast<double>(total)) * 100.0;
            if (cpu > 100.0) cpu = 100.0;
            if (cpu < 0.0) cpu = 0.0;
        }

        // Memory via Win32 GlobalMemoryStatusEx (<0.01 ms)
        MEMORYSTATUSEX memInfo;
        memInfo.dwLength = sizeof(MEMORYSTATUSEX);
        GlobalMemoryStatusEx(&memInfo);

        double total_mb = static_cast<double>(memInfo.ullTotalPhys) / (1024.0 * 1024.0);
        double avail_mb = static_cast<double>(memInfo.ullAvailPhys) / (1024.0 * 1024.0);
        double used_mb = total_mb - avail_mb;
        double mem_pct = static_cast<double>(memInfo.dwMemoryLoad);

        auto t1 = std::chrono::high_resolution_clock::now();
        double us = std::chrono::duration<double, std::micro>(t1 - t0).count();

        return { cpu, mem_pct, total_mb, used_mb, avail_mb, us };
    }
};

int main(int argc, char* argv[]) {
    FastTelemetryProbe probe;
    std::this_thread::sleep_for(std::chrono::milliseconds(100)); // Prime first delta

    bool daemonMode = false;
    std::string outFile = "bin/telemetry.json";
    int intervalMs = 200;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--daemon") {
            daemonMode = true;
        } else if (arg == "--out" && i + 1 < argc) {
            outFile = argv[++i];
        } else if (arg == "--interval" && i + 1 < argc) {
            intervalMs = std::stoi(argv[++i]);
        }
    }

    if (!daemonMode) {
        auto m = probe.Sample();
        std::cout << "{"
                  << "\"cpu_percent\":" << std::fixed << std::setprecision(1) << m.cpu_percent << ","
                  << "\"mem_percent\":" << m.mem_percent << ","
                  << "\"mem_total_mb\":" << std::setprecision(0) << m.mem_total_mb << ","
                  << "\"mem_used_mb\":" << m.mem_used_mb << ","
                  << "\"mem_available_mb\":" << m.mem_available_mb << ","
                  << "\"sample_duration_us\":" << std::setprecision(2) << m.sample_duration_us
                  << "}" << std::endl;
        return 0;
    }

    // Daemon mode: continuously write atomic JSON snapshot
    std::cout << "[FastTelemetryProbe] Started C++ native probe daemon -> " << outFile << std::endl;
    std::string tmpFile = outFile + ".tmp";

    while (true) {
        auto m = probe.Sample();
        {
            std::ofstream out(tmpFile, std::ios::trunc);
            if (out.is_open()) {
                out << "{"
                    << "\"cpu_percent\":" << std::fixed << std::setprecision(1) << m.cpu_percent << ","
                    << "\"mem_percent\":" << m.mem_percent << ","
                    << "\"mem_total_mb\":" << std::setprecision(0) << m.mem_total_mb << ","
                    << "\"mem_used_mb\":" << m.mem_used_mb << ","
                    << "\"mem_available_mb\":" << m.mem_available_mb << ","
                    << "\"sample_duration_us\":" << std::setprecision(2) << m.sample_duration_us << ","
                    << "\"source\":\"native_cpp\""
                    << "}" << std::endl;
                out.close();
                // Atomic rename
                MoveFileExA(tmpFile.c_str(), outFile.c_str(), MOVEFILE_REPLACE_EXISTING);
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(intervalMs));
    }

    return 0;
}
