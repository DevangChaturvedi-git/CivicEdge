"""One-shot job: create the node's TLS material, broker credentials and
signing key. Idempotent: existing files are kept."""
import base64, datetime, hashlib, ipaddress, os, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID
from .common import env, fleet, device_password

def _pw_line(user, pw):
    salt = os.urandom(12)
    h = hashlib.pbkdf2_hmac("sha512", pw.encode(), salt, 101, 64)
    return f"{user}:$7$101${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"

def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

def main(d):
    os.makedirs(d, exist_ok=True)
    p = lambda n: os.path.join(d, n)
    if not os.path.exists(p("ca.crt")):
        nb = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        na = nb + datetime.timedelta(days=825)
        cak = ec.generate_private_key(ec.SECP256R1())
        ca = (x509.CertificateBuilder().subject_name(_name("CivicEdge Node CA"))
              .issuer_name(_name("CivicEdge Node CA")).public_key(cak.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .sign(cak, hashes.SHA256()))
        sk = ec.generate_private_key(ec.SECP256R1())
        san = x509.SubjectAlternativeName([x509.DNSName("broker"), x509.DNSName("localhost"),
                                           x509.IPAddress(ipaddress.ip_address("127.0.0.1"))])
        crt = (x509.CertificateBuilder().subject_name(_name("broker")).issuer_name(ca.subject)
               .public_key(sk.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(nb).not_valid_after(na).add_extension(san, critical=False)
               .sign(cak, hashes.SHA256()))
        pem = serialization.Encoding.PEM
        open(p("ca.crt"), "wb").write(ca.public_bytes(pem))
        open(p("server.crt"), "wb").write(crt.public_bytes(pem))
        open(p("server.key"), "wb").write(sk.private_bytes(
            pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    secret = env("FLEET_SECRET", "change-me")
    lines = [_pw_line("core", env("CORE_MQTT_PASSWORD", "change-me-core"))]
    lines += [_pw_line(dv["id"], device_password(secret, dv["id"])) for dv in fleet()]
    open(p("passwd"), "w").write("\n".join(lines) + "\n")
    # each device may publish only to its own topic; only the core may read
    open(p("acl"), "w").write("user core\ntopic read ce/#\n\npattern write ce/%u/telemetry\n")
    open(p("mosquitto.conf"), "w").write(
        f"per_listener_settings false\nallow_anonymous false\npersistence false\n"
        f"password_file {d}/passwd\nacl_file {d}/acl\nlistener 8883\n"
        f"cafile {d}/ca.crt\ncertfile {d}/server.crt\nkeyfile {d}/server.key\n"
        f"log_dest stdout\nlog_type error\nlog_type warning\n"
        + (f"user {env('BROKER_USER')}\n" if env("BROKER_USER") else ""))
    kd = env("KEY_DIR")
    if kd:
        os.makedirs(kd, exist_ok=True)
        kp = os.path.join(kd, "node_ed25519.key")
        if not os.path.exists(kp):
            k = ed25519.Ed25519PrivateKey.generate()
            open(kp, "wb").write(k.private_bytes(serialization.Encoding.Raw,
                 serialization.PrivateFormat.Raw, serialization.NoEncryption()))
            os.chmod(kp, 0o600)
        k = ed25519.Ed25519PrivateKey.from_private_bytes(open(kp, "rb").read())
        open(p("node_ed25519.pub"), "w").write(k.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
    uid = env("BROKER_UID")
    if uid:  # broker runs unprivileged; secrets readable only by it
        for n in ("passwd", "acl", "server.key", "mosquitto.conf"):
            os.chown(p(n), int(uid), int(uid)); os.chmod(p(n), 0o600)
        for n in ("ca.crt", "server.crt", "node_ed25519.pub"):
            if os.path.exists(p(n)): os.chmod(p(n), 0o644)
    print(f"init ok: {len(lines)} broker identities in {d}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else env("CERT_DIR", "/certs"))
