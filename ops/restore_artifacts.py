"""Bounded trusted-backup restore, preflight before any artifact writes."""
import io
import os
from pathlib import Path
import stat
import sys
import tarfile

LIMIT=260*1024*1024

def empty_root(root):
    root=Path(root)
    info=root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or any(root.iterdir()):
        raise ValueError('Artifact target must be an empty private owned directory')
    return root

def restore(root, stream):
    root=empty_root(root)
    data=stream.read(LIMIT+1)
    if len(data)>LIMIT:
        raise ValueError('Archive size bound exceeded')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        members=[];names=set();total=0
        for member in archive:
            if member.name in {'.','./'} and member.isdir():
                continue
            name=member.name.removeprefix('./')
            if not member.isfile() or '/' in name or name in {'','.','..'} or '\\' in name or member.size<0 or name in names:
                raise ValueError('Only unique flat regular artifact files are allowed')
            total+=member.size
            if total>256*1024*1024 or len(members)>=4096:
                raise ValueError('Artifact bounds exceeded')
            names.add(name);members.append((member,name))
        empty_root(root)
        for member,name in members:
            fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as output:
                source=archive.extractfile(member)
                if source is None:raise ValueError('Missing artifact content')
                with source:
                    while block:=source.read(65536):output.write(block)

if __name__=='__main__':
    root=Path(os.environ['U4_PRIVATE_ARTIFACTS'])
    if '--check-empty' in sys.argv:empty_root(root)
    else:restore(root,sys.stdin.buffer)
