@echo off
REM Windows guest-side launcher for the gfx1201 userspace compute bring-up.
REM Runs amd_gpu_driver.backends.windows.compute_dispatch as a script: cold-boot
REM the GPU from userspace over D3DKMTEscape (serviced by the production
REM amdgpu_wddm KMD that owns the card) and run the self-tests
REM (NOP+fence, WRITE_DATA, noop shader dispatch).
REM
REM Adjust for your guest:
REM   DRIVER_ROOT - the userspace_driver\python checkout (here a virtiofs share Z:)
REM   FW_DIR      - gfx1201 firmware .bin set (GC 12.0.1: PSP SOS/RLC/MEC/SDMA/MES + SMU)
REM   PYTHON      - your Python 3.12 interpreter
set DRIVER_ROOT=Z:\userspace_driver\python
set FW_DIR=Z:\winfw
set PYTHON="C:\Program Files\Python312\python.exe"

REM LITE_MES_RECIPE=1 selects the gfx1201 RLC/IMU autoload firmware-load recipe
REM (TOC-from-SOS + cmd-buffer LOAD_IP_FW + RS64 + RLC_G-last); without it the
REM legacy load path uses the wrong TOC and the autoload never completes.
set LITE_MES_RECIPE=1
set LITE_PSP_VERBOSE=1
set PYTHONPATH=%DRIVER_ROOT%

cd /d %DRIVER_ROOT%
%PYTHON% -u amd_gpu_driver\backends\windows\compute_dispatch.py --device 0 --fw-dir %FW_DIR% 2>&1
echo BRINGUP_EXIT=%errorlevel%
