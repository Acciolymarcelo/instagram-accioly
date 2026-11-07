#!/usr/bin/env python3
"""Set a verified public media origin after its owner authorizes that hosting."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from publisher import validate_public_url, validate_manifest, private_json

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True,
                        help='Real HTTPS directory URL ending in /media/; no credentials or expiring tokens.')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'manifest.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'media-urls.json')
    args = parser.parse_args()
    prefix = args.base_url.rstrip('/') + '/'
    validate_public_url(prefix, resolve=True)
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    result = {'schema_version': 1, 'status': 'configured_pending_live_download_verification', 'pieces': {}}
    for piece in manifest['pieces']:
        slug = piece['slug']
        result['pieces'][slug] = [
            {'number': image['number'], 'sha256': image['sha256'],
             'url': prefix + quote(slug, safe='') + f'/slide_{image["number"]:02d}.jpg'}
            for image in piece['images']
        ]
    private_json(args.output, result)
    print('URLs configured locally. Run authenticated preflight before enabling the executor.')

if __name__ == '__main__':
    main()
