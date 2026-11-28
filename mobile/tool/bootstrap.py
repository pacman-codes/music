"""Generate Flutter platform shells reproducibly, without replacing Dart sources."""
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
ANDROID="http://schemas.android.com/apk/res/android"
ET.register_namespace("android",ANDROID)
def a(name):
    return "{"+ANDROID+"}"+name

with tempfile.TemporaryDirectory() as temp:
    scaffold=Path(temp)/"lain"
    subprocess.run(["flutter","create","--no-pub","--platforms=android,ios",
                    "--org","ink.soodoo","--project-name","lain",str(scaffold)],check=True)
    for platform in ("android","ios"):
        destination=ROOT/platform
        if not destination.exists():
            shutil.copytree(scaffold/platform,destination)

manifest=ROOT/"android/app/src/main/AndroidManifest.xml"
tree=ET.parse(manifest)
root=tree.getroot()
for permission in ("INTERNET","WAKE_LOCK","FOREGROUND_SERVICE","FOREGROUND_SERVICE_MEDIA_PLAYBACK"):
    name="android.permission."+permission
    if not any(n.get(a("name"))==name for n in root.findall("uses-permission")):
        root.insert(0,ET.Element("uses-permission",{a("name"):name}))
app=root.find("application")
assert app is not None
app.set(a("label"),"LAIN")
app.set(a("allowBackup"),"false")
app.set(a("networkSecurityConfig"),"@xml/lain_network_security")
activity=app.find("activity")
assert activity is not None
activity.set(a("name"),"com.ryanheise.audioservice.AudioServiceActivity")
for kind,name,action in [
    ("service","com.ryanheise.audioservice.AudioService","android.media.browse.MediaBrowserService"),
    ("receiver","com.ryanheise.audioservice.MediaButtonReceiver","android.intent.action.MEDIA_BUTTON")]:
    if not any(n.get(a("name"))==name for n in app.findall(kind)):
        node=ET.SubElement(app,kind,{a("name"):name,a("exported"):"true"})
        if kind=="service":node.set(a("foregroundServiceType"),"mediaPlayback")
        filt=ET.SubElement(node,"intent-filter")
        ET.SubElement(filt,"action",{a("name"):action})
tree.write(manifest,encoding="utf-8",xml_declaration=True)
config=ROOT/"android/app/src/main/res/xml/lain_network_security.xml"
config.parent.mkdir(parents=True,exist_ok=True)
config.write_text("""<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="false"/>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="false">127.0.0.1</domain>
        <domain includeSubdomains="false">localhost</domain>
    </domain-config>
</network-security-config>
""")

plist=ROOT/"ios/Runner/Info.plist"
info=plistlib.loads(plist.read_bytes())
info["CFBundleDisplayName"]="LAIN"
info["UIBackgroundModes"]=sorted(set(info.get("UIBackgroundModes",[])+["audio"]))
# just_audio's authenticated stream uses a local loopback proxy. Remote
# origins are validated as HTTPS in Dart. No other HTTP service is intended.
info["NSAppTransportSecurity"]={"NSAllowsLocalNetworking":True}
plist.write_bytes(plistlib.dumps(info))
entitlements=ROOT/"ios/Runner/Runner.entitlements"
entitlements.write_bytes(plistlib.dumps({"keychain-access-groups":[]}))
project=ROOT/"ios/Runner.xcodeproj/project.pbxproj"
text=project.read_text()
# Runner app build configs only, not the test target.
if "CODE_SIGN_ENTITLEMENTS = Runner/Runner.entitlements;" not in text:
    text,count=re.subn(r"(PRODUCT_BUNDLE_IDENTIFIER = ink\.soodoo\.lain;)",
                       r"CODE_SIGN_ENTITLEMENTS = Runner/Runner.entitlements;\n\t\t\t\t\1",text)
    if count!=3:raise RuntimeError("Unexpected Runner project layout")
    project.write_text(text)
print("LAIN platform shells prepared")
