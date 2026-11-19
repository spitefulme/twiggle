"""Emit installed dependency legal notices as JSON for release review."""
import importlib.metadata as metadata
import json
from pathlib import PurePosixPath

result={}
for distribution in metadata.distributions():
    name=distribution.metadata['Name']
    notices={}
    for file in distribution.files or ():
        leaf=PurePosixPath(str(file)).name
        if leaf.upper().startswith(('LICENSE','COPYING','AUTHORS','NOTICE')) and not leaf.endswith(('.py','.pyc')):
            path=distribution.locate_file(file)
            if path.is_file():notices[str(file)]=path.read_text(errors='replace')
    result[name]={'version':distribution.version,'license':distribution.metadata.get('License-Expression') or distribution.metadata.get('License'),'notices':notices}
print(json.dumps(result,indent=2))
