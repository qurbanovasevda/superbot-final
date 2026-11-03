"""Check Gemini network access without reading or transmitting any API key."""
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

HOST='generativelanguage.googleapis.com'


def main():
    print('Gemini network diagnostic - no API key is used.')
    try:
        socket.getaddrinfo(HOST,443,type=socket.SOCK_STREAM)
        print('DNS: OK')
    except OSError:
        print('DNS: FAILED. Try a different network or check DNS settings.')
        return 1
    try:
        with socket.create_connection((HOST,443),timeout=10):
            print('TCP 443: OK')
    except OSError:
        print('TCP 443: FAILED. Check firewall, proxy or network restrictions.')
        return 1
    try:
        # A missing-key HTTP error is expected and proves HTTPS is reachable.
        with urlopen('https://'+HOST+'/v1beta/models',timeout=15) as r:
            print('Python HTTPS: OK (HTTP '+str(r.status)+')')
    except HTTPError as e:
        print('Python HTTPS: reachable (HTTP '+str(e.code)+', no API key sent).')
        if e.code in (401,403):
            print('Authentication/access was rejected; missing credentials are expected in this check.')
        print('This does not verify API key, model access or free quota.')
    except (URLError,OSError) as e:
        reason=e.reason if isinstance(e,URLError) else e
        if isinstance(reason,ssl.SSLCertVerificationError):
            print('Python HTTPS: TLS CERTIFICATE FAILED. Check system clock and trusted certificates.')
        elif isinstance(reason,ssl.SSLError):
            print('Python HTTPS: TLS FAILED. Check HTTPS inspection/proxy settings.')
        elif isinstance(reason,TimeoutError):
            print('Python HTTPS: TIMEOUT. Try a different network.')
        else:
            print('Python HTTPS: CONNECTION FAILED. Check network/proxy settings.')
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
