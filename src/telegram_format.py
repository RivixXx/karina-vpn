import re

_RESERVED = re.compile(r"([_*\[\]()~`>#+\-=|{}.!])")

def markdown_v2_escape(value):
    return _RESERVED.sub(r"\\\1", str(value).replace("\\", "\\\\"))
