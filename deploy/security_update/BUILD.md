# Building the installer package

The zip given to the server is this folder plus:
- `payload/` – the changed/new files, same relative paths as the server root
- `wheels/` – Windows wheels for new dependencies (cryptography, cp39-abi3 and cp311-abi3, win_amd64)

Run `INSTALL_SECURITY_UPDATE.cmd` as administrator on the server.
