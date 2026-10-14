# Printer trust roots

`bambu-ca.pem` contains public Bambu Lab certificate authorities, obtained from
[OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI/blob/main/examples/ca_cert.pem).
It contains no printer certificates, private keys, or local configuration.
The LAN adapter verifies the certificate chain and the printer serial as the TLS
server identity for both MQTT and FTPS. Operators can supply a different trust
bundle with `DREAMER_PRINTER_CA_FILE`.
