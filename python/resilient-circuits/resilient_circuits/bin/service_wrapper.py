#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Service wrapper.  Runs the main python application from a Windows service."""

import win32service
import win32serviceutil
import win32api
import win32con
import win32job
import subprocess
import os
import signal

SERVICE_NAME = "RESIL_SVC"
SERVICE_DISPLAY_NAME = "Resilient Circuits"


class irms_svc(win32serviceutil.ServiceFramework):
    """The service implementation"""

    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    process_handle = None

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.isAlive = True

    def SvcDoRun(self):
        import servicemanager
        servicemanager.LogInfoMsg(self._svc_name_ + " Start Requested")
        try:
            hJob = win32job.CreateJobObject(None, "")
            extended_info = win32job.QueryInformationJobObject(hJob, win32job.JobObjectExtendedLimitInformation)
            extended_info['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(hJob, win32job.JobObjectExtendedLimitInformation, extended_info)
            self.process_handle = subprocess.Popen(("resilient-circuits.exe", "run"))
            # Convert process id to process handle:
            perms = win32con.PROCESS_TERMINATE | win32con.PROCESS_SET_QUOTA
            hProcess = win32api.OpenProcess(perms, False, self.process_handle.pid)
            win32job.AssignProcessToJobObject(hJob, hProcess)
        except:
            servicemanager.LogErrorMsg(self._svc_name_ + " failed to launch resilient-circuits.exe")
            raise
        servicemanager.LogInfoMsg(self._svc_name_ + " Started")

        while self.isAlive:
            if self.process_handle.poll() != None:
                self.SvcStop()
            win32api.SleepEx(10000, True)

    def SvcStop(self):
        import servicemanager
        msg = "stopping."
        try:
            # There's no way to say this nicely
            self.process_handle.terminate()
        except Exception as exc:
            msg = exc.message
            pass
        servicemanager.LogInfoMsg(self._svc_name_ + " - Received stop signal; " + msg)
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.isAlive = False


def ctrlHandler(ctrlType):
    return True


if __name__ == '__main__':
    win32api.SetConsoleCtrlHandler(ctrlHandler, True)
    win32serviceutil.HandleCommandLine(irms_svc)