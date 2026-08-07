"""One TLS pair, for the docker agent's own way of publishing a virtual service.

Real material rather than a plausible-looking string, and generated once rather
than per test: `generate()` reads the certificate to check `sv_hostname`
against it, so a fixture that only looked like PEM would exercise the refusal
path in every test that meant to exercise the accepting one.

One builder, for the same reason `evidence_fixtures.py` is one: two fixtures for
one schema, with different defaults, is how a test ends up asserting against
something no other test would recognise.

Generated with, and reproducible by:

    openssl req -x509 -newkey rsa:2048 -keyout sv-tls.key -out sv-tls.crt \
        -days 7300 -nodes -subj "/CN=mocks.example.com/O=bzm-opl-gen test fixture" \
        -addext "subjectAltName=DNS:mocks.example.com,DNS:*.mocks.example.com"

**Nothing here is a secret**, and it is checked in deliberately: it is a
self-signed key for a domain nobody owns, which is what makes it safe to commit
and useless to steal. Its expiry is 2045 and nothing here would care if it were
yesterday -- `cert.py` reads names and asks nothing about validity, which is a
scope decision rather than an oversight.
"""

# The names the certificate carries: the SAN's two entries, then the Common
# Name, which duplicates the first. Stated so a test can assert the *reader*
# rather than assert against whatever the reader happened to return.
SV_HOST = "mocks.example.com"
SV_WILDCARD_HOST = "anything.mocks.example.com"
SV_NAMES = ["mocks.example.com", "*.mocks.example.com"]
# A host the certificate does not cover, at the depth a wildcard cannot reach.
SV_WRONG_HOST = "mocks.example.org"

SV_CERT = """-----BEGIN CERTIFICATE-----
MIIDlDCCAnygAwIBAgIUKQ9DtZ2fa7nGWB7YjvJh86ouIaUwDQYJKoZIhvcNAQEL
BQAwPzEaMBgGA1UEAwwRbW9ja3MuZXhhbXBsZS5jb20xITAfBgNVBAoMGGJ6bS1v
cGwtZ2VuIHRlc3QgZml4dHVyZTAeFw0yNjA4MDcwMDExMjBaFw00NjA4MDIwMDEx
MjBaMD8xGjAYBgNVBAMMEW1vY2tzLmV4YW1wbGUuY29tMSEwHwYDVQQKDBhiem0t
b3BsLWdlbiB0ZXN0IGZpeHR1cmUwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK
AoIBAQCcbNJoCTKdhJcLpFcuaKFEG+JN5C2c7FAK3hHWis7MmoDKX8UOM9+jPWdO
LOUBnyqKXRxYrGt2LLx1+4B8cUR9ub/RgBxBv5D7qdfWeE+HiYkxhwFuW1uQIaAS
09P3WivZgqjIoSOSkZ9Lpo/p4K3Bs3wg00pHZyk+nbI3eCG2iBBN1G135Ibh9uHM
jaDvl3WO6P7Q+aD/tcWlLNmzNYKfQiU6a1vHxHKQWiHGk3foO3oSH3DooUzssH4i
yKJzXmcSriBCofY5SZC+fqfKRufPEspcIZd+xQsYdlmWVyDcRyFXuLgGrpkFUX/V
ZpC4ZbRgjjqH0LiNpNf/LLvnuK/XAgMBAAGjgYcwgYQwHQYDVR0OBBYEFI4RFXkZ
mjiAr6ff6w9Q+5e3Am3RMB8GA1UdIwQYMBaAFI4RFXkZmjiAr6ff6w9Q+5e3Am3R
MA8GA1UdEwEB/wQFMAMBAf8wMQYDVR0RBCowKIIRbW9ja3MuZXhhbXBsZS5jb22C
EyoubW9ja3MuZXhhbXBsZS5jb20wDQYJKoZIhvcNAQELBQADggEBAAYKU5BNqrXk
Ky8AZW+RM2Ubw9xn3HvlhVzzrFpz7bKTNQZYgWnKftnMttVxCkVgcGLd/qlN/ntr
ogqeVs8TI0Cl31VFEj1FMOdBXbz85PamhzN6dqn34rp0cbOZJaJ5wH2CTqvQMz/0
VTgDCI+TjpD2I8gTv61B/TTs9enxG473kbyPND7ikOCsGM/CuKTmbtzknNN5+OdS
Q1e34VhycTTBPqU6jUMdyC6mYrYMGBPReY7wAkDXkc9gn6qdONX0OUm9Jg4VoeNu
86SIcvmvJRccy8TwXbCuyIjEsGlAOFKcKpnBHY+vSupwpDOtsxMTr/5cOwTzgA3L
QSOHnhczQDs=
-----END CERTIFICATE-----
"""

# PKCS#8, which is what BlazeMeter require -- the header is the whole
# discriminator, and the PKCS#1 export below is the one a validator has to
# refuse by name.
SV_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCcbNJoCTKdhJcL
pFcuaKFEG+JN5C2c7FAK3hHWis7MmoDKX8UOM9+jPWdOLOUBnyqKXRxYrGt2LLx1
+4B8cUR9ub/RgBxBv5D7qdfWeE+HiYkxhwFuW1uQIaAS09P3WivZgqjIoSOSkZ9L
po/p4K3Bs3wg00pHZyk+nbI3eCG2iBBN1G135Ibh9uHMjaDvl3WO6P7Q+aD/tcWl
LNmzNYKfQiU6a1vHxHKQWiHGk3foO3oSH3DooUzssH4iyKJzXmcSriBCofY5SZC+
fqfKRufPEspcIZd+xQsYdlmWVyDcRyFXuLgGrpkFUX/VZpC4ZbRgjjqH0LiNpNf/
LLvnuK/XAgMBAAECggEACIpafIqlWaX6bIzMTmuGWTEf4c/SUQ3dRZKgAl4xcfNX
knUNzbuK+3R9S5k2PFND39UQmKN6pIjxwIv66mXGxF8xYroDZkWUIAwYYuHOZHXf
veADSygFb5ueYUNxKxaga++Z0TEXFPK1cfcbRrzZJR5sabW6NFF6FiycT9ypE+Lz
t/0gdLQs48rIORZHJV2fJE72dEaBirKU9NfirPgW1A+6zo5kIRSLT4qn2yTvpk5F
OyTe8dXxXEwTRgc1V4cTksVE2tDwoN2Y9wYv17c/AX793u82nq+LvtofWr4OMfEd
qFeeY3OkCxVXzwq2nLBXDJW+C4IM56S0VWq7h8/TPQKBgQDKDVX+GqLsb3u8Jsnw
hFheoPt8XtFtA/uWEIO8DqJcmf7G6EXkxzYFtEaen9aFUIDulz/VQ4Sg6vu5tEko
lTMCrSUDMFZsSbZZDvPGcD+7zEObdD7bENtHZXo9Miuk3b2DdWXFc5jDTHbCQRPF
Uax9RNQBTqXSehjdH3rs6yExFQKBgQDGMMn0ahPIsQLRtfIInJrz7M+0RZx3AaUO
oK33stpZrV2uvaoSe8SwITP6Kzl5fEXmZV3tIcNt5nzitizpFGHzMATv5YjVQDnV
ioTePAhJ8nkvB8PQE198GhEYNy+4zshqCnDlgCjXo0SWUkMOOCm9Ed5/3jcRT7tw
l6r3w6DgOwKBgQCPXeMUiIXuSjR8DvHfDak+i++mEgl71wWfN1yiBahDGKnlLU7a
xFeauI4bY8LtmW/C2+NZSa1EGThATqJSf3tQfNb0akoIUE1o5+klduRiNtAJ7/Ph
sRZGlMSlw4GgXA5qxtRNxHYyrYDe2RpUOl2wDTR5MPsMW8JktD+e+D/2+QKBgBLD
uctPY9IjmE28uU7BbRZdPIkn8hl+aV9KLU5/e5b0CCOsR3b6ivPWIPK1tvpensui
m1MBWFyGbxqT/wqOaHu69yyzgdIXA6LJO61C59IAiCLAzHRd8TNx0F6HkxgfU/Be
TrQb/0HzbmIBJeIpxSHmmDdpFbOo5elSItjUh93TAoGBAK2g8p9T5fh00A5+5n+h
vJuvZP7S195p0Mtrk3fp9R0LPDhGdiTabfcimLwQJYmfZAztFhGKN6q0F6lmlDkK
vXsvSUxbi1GTKKNqc/ufiFO3/JKIAmUpb2kJa8qv+694N8iJW2QVErXW1AHFzP3M
Lsk5TSeKuN6Uj7GMCDG+GKZY
-----END PRIVATE KEY-----
"""

# The same key, as `openssl genrsa` and `openssl rsa -traditional` write one.
# The common export, and an agent handed it starts, reports online and fails
# at the first TLS handshake.
SV_KEY_PKCS1 = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAnGzSaAkynYSXC6RXLmihRBviTeQtnOxQCt4R1orOzJqAyl/F
DjPfoz1nTizlAZ8qil0cWKxrdiy8dfuAfHFEfbm/0YAcQb+Q+6nX1nhPh4mJMYcB
bltbkCGgEtPT91or2YKoyKEjkpGfS6aP6eCtwbN8INNKR2cpPp2yN3ghtogQTdRt
d+SG4fbhzI2g75d1juj+0Pmg/7XFpSzZszWCn0IlOmtbx8RykFohxpN36Dt6Eh9w
6KFM7LB+Isiic15nEq4gQqH2OUmQvn6nykbnzxLKXCGXfsULGHZZllcg3EchV7i4
Bq6ZBVF/1WaQuGW0YI46h9C4jaTX/yy757iv1wIDAQABAoIBAAiKWnyKpVml+myM
zE5rhlkxH+HP0lEN3UWSoAJeMXHzV5J1Dc27ivt0fUuZNjxTQ9/VEJijeqSI8cCL
+uplxsRfMWK6A2ZFlCAMGGLhzmR1373gA0soBW+bnmFDcSsWoGvvmdExFxTytXH3
G0a82SUebGm1ujRRehYsnE/cqRPi87f9IHS0LOPKyDkWRyVdnyRO9nRGgYqylPTX
4qz4FtQPus6OZCEUi0+Kp9sk76ZORTsk3vHV8VxME0YHNVeHE5LFRNrQ8KDdmPcG
L9e3PwF+/d7vNp6vi77aH1q+DjHxHahXnmNzpAsVV88KtpywVwyVvguCDOektFVq
u4fP0z0CgYEAyg1V/hqi7G97vCbJ8IRYXqD7fF7RbQP7lhCDvA6iXJn+xuhF5Mc2
BbRGnp/WhVCA7pc/1UOEoOr7ubRJKJUzAq0lAzBWbEm2WQ7zxnA/u8xDm3Q+2xDb
R2V6PTIrpN29g3VlxXOYw0x2wkETxVGsfUTUAU6l0noY3R967OshMRUCgYEAxjDJ
9GoTyLEC0bXyCJya8+zPtEWcdwGlDqCt97LaWa1drr2qEnvEsCEz+is5eXxF5mVd
7SHDbeZ84rYs6RRh8zAE7+WI1UA51YqE3jwISfJ5LwfD0BNffBoRGDcvuM7Iagpw
5YAo16NEllJDDjgpvRHef943EU+7cJeq98Og4DsCgYEAj13jFIiF7ko0fA7x3w2p
PovvphIJe9cFnzdcogWoQxip5S1O2sRXmriOG2PC7ZlvwtvjWUmtRBk4QE6iUn97
UHzW9GpKCFBNaOfpJXbkYjbQCe/z4bEWRpTEpcOBoFwOasbUTcR2Mq2A3tkaVDpd
sA00eTD7DFvCZLQ/nvg/9vkCgYASw7nLT2PSI5hNvLlOwW0WXTyJJ/IZfmlfSi1O
f3uW9AgjrEd2+orz1iDytbb6Xp7LoptTAVhchm8ak/8Kjmh7uvcss4HSFwOiyTut
QufSAIgiwMx0XfEzcdBeh5MYH1PwXk60G/9B825iASXiKcUh5pg3aRWzqOXpUiLY
1Ifd0wKBgQCtoPKfU+X4dNAOfuZ/obybr2T+0tfeadDLa5N36fUdCzw4RnYk2m33
Ipi8ECWJn2QM7RYRijeqtBepZpQ5Cr17L0lMW4tRkyijanP7n4hTt/ySiAJlKW9p
CWvKr/uveDfIiVtkFRK11tQBxcz9zC7JOU0nirjelI+xjAgxvhimWA==
-----END RSA PRIVATE KEY-----
"""

# A certificate with no dNSName in its SAN and no Common Name -- subject `/O=`
# and nothing else. The case the two easy readings collapse: it parses, so it is
# *read*, and what it says is that it covers no host at all. `[]`, never None.
SV_CERT_NO_NAMES = """-----BEGIN CERTIFICATE-----
MIIDJzCCAg+gAwIBAgIUe/VDu9HrUthvreEo/jl1ifgJcicwDQYJKoZIhvcNAQEL
BQAwIzEhMB8GA1UECgwYYnptLW9wbC1nZW4gdGVzdCBmaXh0dXJlMB4XDTI2MDgw
NzAwMTI1N1oXDTQ2MDgwMjAwMTI1N1owIzEhMB8GA1UECgwYYnptLW9wbC1nZW4g
dGVzdCBmaXh0dXJlMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv0Sd
GfuSIcD3VfLzRixtA+13qR7yAj62hI/WhASW/4asSIPKZEVQhOZDxFkHezKPnGgr
MhmZjrR8SQmavR6bnQyjya5WV5x+Vs2Y1A7wx+jLROE11LOwBNhCrHjDjsGWoExO
J9w+mZr44AdxrrF3Oqq4Bd5URhQfoxThEnQjXlkOTpkAmVRPUhun3RO9b9/4oujU
gfz0ZzchXIkZeK9+X0yXbcJVx9AQPp9wbFiMyqFs5vUKl6dQa51EODqqhVI6ASjH
dw0dCStH9ImO0Jy/9Thmukid91OMZuQtKRUNV8Ac5afN3370ea6RNNvaqlRmJSGA
fjdS7MbOrgNtEESMswIDAQABo1MwUTAdBgNVHQ4EFgQUbkWwFSaKQyv5lnBx1vfi
NHPPGbcwHwYDVR0jBBgwFoAUbkWwFSaKQyv5lnBx1vfiNHPPGbcwDwYDVR0TAQH/
BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEASMRchu1zpdcTRfhyqp1Qf5BXN7WN
G5+vji9UnxnAyBv/m7kNYy1SK7wxQMdaXJSpIyN/IA1QXtHODh3F8dubY39lpjRd
EWCXS9IWgYnZu3+fOiE4MMNo4sYnG/ak412hjDjUD9rRuI65k4ZzK+lvyxEDwzFM
NbvErBrS6ImlObg6jdx0S2gKPNlZRXwsh8+9iHGBCv0A4FnuDp9sEi+sPzeZFP3O
H91zpnXlBoEQMlW4rStGZ0B7mmpOezfFLV77wr+QJUN/18RH2QX7BJmLqIgm1xoV
HlO2SV9r7RdDeJ6nFKhMIAW+0dU25gyRVZaCXGnyDWu6Dyy3MlfXSdz48g==
-----END CERTIFICATE-----
"""
