# ROCm Directory Map

This document maps out where all ROCm-related directories live on this system and any remote system.

**Update the paths below to match your actual setup.**

## Environment Setup

**Python Environment:** Claude Code is launched with the project venv already active (symlinked as `venv/` in this workspace). This venv contains required build tools including:
- meson (for building simde, libdrm, and other meson-based dependencies)
- Other Python dependencies from requirements.txt

## Repository Aliases

These aliases are used by `/stage-review` and other commands to resolve short names to paths.

| Alias | Path | Notes |
|-------|------|-------|
| therock | D:\R | Main ROCm build repo |
| rocm-kpack | D:\R\base\rocm-kpack | Kernel packaging tools (submodule)|
| rocm-systems | D:\R\rocm-systems | ROCm Systems Superrepo (submodule)|
| rocm-libraries | D:\R\rocm-libraries | ROCm Libraris Superrepo (submodule) |
| jax | D:\R\jax | JAX framework |
| xla | D:\R\xla | XLA compiler |
| amdgpu | C:\Users\nod\github\amdgpu   | Linux amdgpu driver |
| workspace | C:\Users\nod\github\claude-rocm-workspace | This meta-workspace |

## Build Trees

### Active Builds
- **Main build:** `D:\R\therock-build`
  - Configuration: Release
  - Target architecture: [gfx1201]
  - CMake flags:
  - Built ROCm installation is under `dist/rocm`

## Remote Environment Setup for node sharkmi300x

**Remote Access:** Remote access is already setup with ssh keys and ssh / scp commands should just work referecning the hostname: sharkmi300x

**Python Environment:** Claude Code will need to activate the remote Python environment /home/anush/github/TheRock/.venv everytime it logs into the system . This venv contains required build tools including:
- meson (for building simde, libdrm, and other meson-based dependencies)
- Other Python dependencies from requirements.txt

## Repository Aliases

These aliases are used by `/stage-review` and other commands to resolve short names to paths.

| Alias | Remote Path | Notes |
|-------|------|-------|
| remote-therock | /home/anush/github/TheRock | Main ROCm build repo |
| remote-eocm-kpack | /home/anush/github/TheRock/base/rocm-kpack | Kernel packaging tools (submodule)|
| remote-rocm-systems | /home/anush/github/TheRock/rocm-systems | ROCm Systems Superrepo (submodule)|
| remote-eocm-libraries | /home/anush/github/TheRock/rocm-libraries | ROCm Libraris Superrepo (submodule) |
| remote-jax | /home/anush/github/TheRock/jax | JAX framework |
| remote-xla | /home/anush/github/TheRock/xla | XLA compiler |

## Build Trees

### Active Builds
- **Main build:** `D:\R\therock-build`
  - Configuration: Release
  - Target architecture: [gfx942]
  - CMake flags:
  - Built ROCm installation is under `dist/rocm`
