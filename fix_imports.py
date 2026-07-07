import re, glob
for f in glob.glob("connectors/proto/*_pb2.py"):
    with open(f, encoding="utf-8") as fh:
        text = fh.read()
    fixed = re.sub(r"^import (\w+_pb2) as", r"from . import \1 as", text, flags=re.M)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(fixed)
    print("поправлен:", f)
