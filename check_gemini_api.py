"""Send one minimal Gemini request using project settings; never print the key."""
import json
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from app.config import Settings


def main():
    settings=Settings()
    key=settings.gemini_api_key.strip()
    model=settings.gemini_model
    print('Minimal Gemini API check: one generation request.')
    if not key:
        print('KEY_MISSING: Fill GEMINI_API_KEY in the project .env file.')
        return 1
    if not re.fullmatch(r'gemini-[A-Za-z0-9.-]+',model):
        print('INVALID_MODEL_NAME: Check GEMINI_MODEL in .env.')
        return 1
    print('Key: loaded (hidden)')
    print('Model: '+model)
    body={'contents':[{'parts':[{'text':'Reply with exactly OK.'}]}],
          'generationConfig':{'maxOutputTokens':128}}
    req=Request(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                data=json.dumps(body).encode(),method='POST',
                headers={'x-goog-api-key':key,'Content-Type':'application/json'})
    started=time.monotonic()
    try:
        with urlopen(req,timeout=45) as response:
            payload=json.loads(response.read(65536))
        candidates=payload.get('candidates',[])
        if not candidates:
            print('NO_CANDIDATE: Gemini returned no answer.')
            return 1
        candidate=candidates[0]
        answer=''.join(p.get('text','') for p in candidate.get('content',{}).get('parts',[]) if not p.get('thought'))
        print('HTTP: 200')
        print('Finish: '+str(candidate.get('finishReason','unknown')))
        print('Reply: '+answer.replace(key,'[REDACTED]')[:200])
        print('Elapsed: '+str(round(time.monotonic()-started,1))+' seconds')
        return 0 if answer.strip() else 1
    except HTTPError as e:
        print('HTTP: '+str(e.code))
        try:
            raw=e.read(8192).decode('utf-8',errors='replace')
            raw=raw.replace(key,'[REDACTED]')
            raw=re.sub(r'AIza[A-Za-z0-9_-]+','[REDACTED]',raw)
            print('Google response: '+raw[:1200])
        except OSError:
            print('Google response body unavailable.')
    except (URLError,OSError) as e:
        reason=e.reason if isinstance(e,URLError) else e
        if isinstance(reason,ssl.SSLCertVerificationError):code='TLS_CERTIFICATE_ERROR'
        elif isinstance(reason,socket.gaierror):code='DNS_ERROR'
        elif isinstance(reason,TimeoutError):code='TIMEOUT'
        else:code='CONNECTION_ERROR'
        print(code+' after '+str(round(time.monotonic()-started,1))+' seconds')
    except (ValueError,TypeError):
        print('INVALID_JSON_RESPONSE: Response was not valid Gemini JSON.')
    return 1


if __name__=='__main__':
    raise SystemExit(main())
