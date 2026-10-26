"""Seed Replit App Storage with a verified existing library, without re-embedding."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from finwhiz.config import Settings, AppError, digest
from finwhiz.library_persistence import CloudLibrary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',required=True)
    parser.add_argument('--sha256',required=True)
    args=parser.parse_args()
    data=Path(args.archive).read_bytes()
    if digest(data)!=args.sha256:
        raise AppError('Archive checksum differs. No cloud files were changed.')
    settings=Settings(_env_file=Path(__file__).resolve().parents[1]/'.env')
    print(json.dumps(CloudLibrary(settings).save_archive(data)))
