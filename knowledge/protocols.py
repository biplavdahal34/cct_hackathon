"""Per-device inspection protocols: the exact menu path to open, and the number
or screen that means walk away. Chosen from the device category and brand, so an
iPhone gets Parts & Service History and a Samsung gets the *#0*# test menu.

The model's own in_person_checks are kept on the report; these steps come first
because they are specific and verified by a person, not generated per request."""
import re

from knowledge import nepal

REVIEWED = "2026-10-24"


def platform_for(category: str, *texts) -> str:
    """'ios' | 'samsung' | 'android' | 'mac' | 'windows' | 'desktop' | 'unknown'."""
    blob = " ".join(str(t) for t in texts if t).lower()
    category = (category or "").lower()
    if category == "phone":
        if re.search(r"\b(iphone|apple)\b", blob):
            return "ios"
        if re.search(r"\b(samsung|galaxy)\b", blob):
            return "samsung"
        return "android"
    if category == "laptop":
        if re.search(r"\b(macbook|mac\s?book|apple)\b", blob):
            return "mac"
        return "windows"
    if category == "desktop":
        return "desktop"
    return "unknown"


# Each step: title · how (exact path) · bad_if (the walk-away condition).
_PHONE_COMMON = [
    nepal.NTA_STEP,
    nepal.IMEI_MATCH_STEP,
    nepal.SIM_STEP,
    {"title": "Test the screen with a plain white and a plain black page",
     "how": "Open a blank white note and a black wallpaper at full brightness. Look for dark patches, lines, pink or "
            "green tints, and bright spots. Drag a finger slowly across the whole screen in an app that draws.",
     "bad_if": "Shadowed patches (burn-in), stuck lines, or spots where the touch does not register."},
    {"title": "Cameras and flash",
     "how": "Take a photo with every lens (wide, ultra-wide, telephoto, selfie), switch on the flash, record 10 seconds of video.",
     "bad_if": "Blur that does not clear, dust spots in the same place on every photo, a lens that will not focus."},
    {"title": "Charging and sound",
     "how": "Plug in a charger and watch the battery icon. Play a song on the speaker and on a call. Test the microphone with a voice memo.",
     "bad_if": "Charging stops and starts, crackling speaker, or your recorded voice sounds muffled."},
]

_PHONE_IOS = [
    {"title": "Battery Health",
     "how": "Settings › Battery › Battery Health & Charging. Read Maximum Capacity.",
     "bad_if": "Below 80%, or the screen says 'Unable to verify this iPhone has a genuine Apple battery'."},
    {"title": "Parts and Service History",
     "how": "Settings › General › About. If the phone has had a part replaced since iOS 15.2, a 'Parts and Service History' "
            "line appears here listing Battery, Display, Camera or other parts.",
     "bad_if": "A part is marked 'Unknown Part'. That is an aftermarket or unverified replacement, whatever the seller says about 'non open'."},
    {"title": "Face ID and True Tone",
     "how": "Settings › Face ID & Passcode: enrol your face, lock and unlock. Then Control Centre › long-press brightness: the True Tone switch should be there.",
     "bad_if": "Face ID will not enrol ('Face ID is not available'), or True Tone is missing. Both mean a repair that Apple did not do."},
    {"title": "Activation Lock",
     "how": "Settings › [the seller's name at the top] › Sign Out. The seller must sign out of their Apple ID in front of you, then erase the phone: "
            "Settings › General › Transfer or Reset iPhone › Erase All Content and Settings.",
     "bad_if": "The seller cannot sign out, does not know the password, or the phone restarts to a screen asking for a previous owner's Apple ID."},
    {"title": "Model number and region",
     "how": "Settings › General › About › Model Number. Tap it once to switch between the part number (MLPF3J/A) and the A-number (A2650).",
     "bad_if": "A US model from the iPhone 14 onward (no SIM tray) when you need a physical SIM, or a Japan part number (J/A) if the shutter sound matters to you."},
]

_PHONE_SAMSUNG = [
    {"title": "Hidden test menu",
     "how": "Open the dialler and type *#0*#. Tap RED, GREEN, BLUE and BLACK to check the screen, then TOUCH to draw over the whole screen, "
            "then SENSOR, SPEAKER, VIBRATION and the camera tests.",
     "bad_if": "Patches or lines on the solid colours, areas the touch test cannot fill, a sensor that stays at zero."},
    {"title": "Battery",
     "how": "Settings › Battery and device care › Diagnostics (or Samsung Members app › Support › Phone diagnostics › Battery status).",
     "bad_if": "Battery status 'Weak' or 'Bad', or the phone loses more than a few percent during the 15 minutes you test it."},
    {"title": "Knox and warranty bit",
     "how": "Power off, then hold Volume Down + Volume Up and plug in a USB cable to open Download mode. Read the 'KG STATE' / 'Warranty Void' line, then hold Volume Down to exit.",
     "bad_if": "'Warranty Void: 1' or KG STATE 'Prenormal'. The first means a custom firmware was flashed; the second means the phone is tied to a shop's financing lock."},
    {"title": "Google account and FRP",
     "how": "Settings › Accounts and backup › Manage accounts. The seller removes every Google and Samsung account in front of you, then factory resets: "
            "Settings › General management › Reset › Factory data reset.",
     "bad_if": "After the reset the phone asks for 'the Google account previously synced on this device'. That is an FRP lock and it is not yours to use."},
    {"title": "Model number and region",
     "how": "Settings › About phone › Model number (SM-S908B/DS and so on).",
     "bad_if": "A US carrier code (U, V, T, A) without proof it is unlocked, or a Korean N code if the shutter sound matters."},
]

_PHONE_ANDROID = [
    {"title": "Battery",
     "how": "Settings › Battery (Xiaomi/Redmi: Settings › Battery › Battery health; OnePlus/Oppo/Vivo: Settings › Battery › Battery health). "
            "If there is no health screen, install AccuBattery and look at the estimated capacity after a charge.",
     "bad_if": "Below 80% estimated capacity, or the phone gets hot while idle."},
    {"title": "Screen and touch",
     "how": "Many Android phones have a hidden test menu: Xiaomi *#*#64663#*#*, OnePlus *#808#, Oppo/Realme *#899#, Vivo *#558#. Otherwise use a drawing app over the whole screen.",
     "bad_if": "Dead zones, ghost touches, lines or tints on a plain white screen."},
    {"title": "Google account and FRP",
     "how": "Settings › Accounts (or Passwords & accounts). The seller removes every account in front of you, then factory resets.",
     "bad_if": "After the reset the phone asks for a previously synced Google account. Xiaomi phones can also carry a Mi Account lock; the seller must sign out of that too."},
    {"title": "Bootloader and software",
     "how": "Settings › About phone, tap Build number seven times, then Developer options › OEM unlocking. Also check Settings › Security for the update date.",
     "bad_if": "A custom ROM you did not ask for, or security updates that stopped years ago on a phone sold as recent."},
]

_LAPTOP_COMMON = [
    {"title": "Every key, every port",
     "how": "Open a text editor and press every key including function keys and the number row. Plug something into each USB port, the headphone jack, HDMI and the card reader.",
     "bad_if": "Any key that needs force or repeats, any port that does not connect."},
    {"title": "Screen",
     "how": "Full brightness on a white page and a black page. Open and close the lid slowly several times while watching the picture.",
     "bad_if": "Dead pixels, backlight bleed, pressure marks, or a picture that flickers when the hinge moves."},
    {"title": "Heat and fan under load",
     "how": "Run something heavy for five minutes (a 4K YouTube video and a browser benchmark are enough). Listen to the fan and feel the keyboard deck.",
     "bad_if": "Grinding fan, an uncomfortably hot deck, or the machine slowing down visibly."},
    {"title": "Charger",
     "how": "Plug in the charger that comes with it and confirm the battery icon shows charging. Check the charger brand and wattage against the laptop's label.",
     "bad_if": "A cheap third-party charger with lower wattage, or a charger that only charges when the cable is held at an angle."},
]

_LAPTOP_MAC = [
    {"title": "Battery cycle count",
     "how": "Apple menu › About This Mac › More Info › System Report › Power. Read Cycle Count and Condition.",
     "bad_if": "Condition 'Service Recommended', or a cycle count that does not fit the age the seller claims (about 300 cycles a year of daily use)."},
    {"title": "Activation Lock and Find My",
     "how": "System Settings › [Apple ID] › the seller signs out of iCloud in front of you and turns off Find My Mac, then erases: System Settings › General › Transfer or Reset › Erase All Content and Settings.",
     "bad_if": "The Mac restarts to an Activation Lock screen asking for a previous owner's Apple ID."},
    {"title": "Storage health",
     "how": "System Report › NVMExpress (or Storage): check SMART status. Also Disk Utility › First Aid on the internal disk.",
     "bad_if": "SMART 'Failing', or First Aid reporting errors it cannot fix."},
    {"title": "Keyboard and trackpad",
     "how": "Type a full paragraph. Force-click on the trackpad, three-finger drag, and check the trackpad clicks everywhere.",
     "bad_if": "Repeated or missed letters on the butterfly keyboards (2016–2019 models), or a trackpad that only clicks on one side."},
]

_LAPTOP_WINDOWS = [
    {"title": "Battery report",
     "how": "Open Command Prompt and run: powercfg /batteryreport. Open the saved HTML file and compare Design Capacity with Full Charge Capacity.",
     "bad_if": "Full charge below 80% of design capacity, or a cycle count far above what the seller's 'light use' suggests."},
    {"title": "Drive health",
     "how": "Open Command Prompt as administrator: wmic diskdrive get model,status. For detail install CrystalDiskInfo and read Health Status and Power On Hours.",
     "bad_if": "Status 'Pred Fail', CrystalDiskInfo 'Caution' or 'Bad', or power-on hours that contradict the claimed age."},
    {"title": "Specs match the listing",
     "how": "Settings › System › About (CPU and RAM); Task Manager › Performance › GPU (dedicated graphics model); dxdiag for the display.",
     "bad_if": "A different CPU generation, less RAM, or integrated graphics when a dedicated GPU was advertised."},
    {"title": "Windows activation and BitLocker",
     "how": "Settings › System › Activation should say Windows is activated. Settings › Privacy & security › Device encryption: it must be off or the seller must give you the recovery key.",
     "bad_if": "'Windows is not activated', or an encrypted drive the seller cannot unlock."},
]

_DESKTOP = [
    {"title": "Boot to BIOS and read the parts",
     "how": "Restart and press Del or F2. The BIOS shows CPU model, RAM size and speed, and the drives. Then in Windows open Task Manager › Performance for the GPU model.",
     "bad_if": "Any part differs from the listing. A printed GPU label is not proof; the software must agree."},
    {"title": "Stress for ten minutes",
     "how": "Run a GPU benchmark (Heaven, Superposition or a demanding game) with HWMonitor open. Watch temperatures and listen for coil whine and fan noise.",
     "bad_if": "GPU above 85 °C or CPU above 90 °C under load, crashes, artefacts on screen, or a fan that rattles."},
    {"title": "Drive health",
     "how": "CrystalDiskInfo on every drive: Health Status and Power On Hours.",
     "bad_if": "'Caution' or 'Bad', or hours that contradict the claimed age."},
    {"title": "Power supply",
     "how": "Read the sticker on the PSU: brand, wattage, 80 Plus rating. Check that the GPU power cables are seated.",
     "bad_if": "A no-name PSU with a high-end GPU. A failing PSU takes other parts with it."},
    {"title": "Windows licence",
     "how": "Settings › System › Activation.",
     "bad_if": "Not activated, or activated with a tool the seller cannot explain."},
]

_BY_PLATFORM = {
    "ios": _PHONE_IOS + _PHONE_COMMON,
    "samsung": _PHONE_SAMSUNG + _PHONE_COMMON,
    "android": _PHONE_ANDROID + _PHONE_COMMON,
    "mac": _LAPTOP_MAC + _LAPTOP_COMMON,
    "windows": _LAPTOP_WINDOWS + _LAPTOP_COMMON,
    "desktop": _DESKTOP,
}

_PLATFORM_LABEL = {
    "ios": "iPhone", "samsung": "Samsung phone", "android": "Android phone",
    "mac": "MacBook", "windows": "Windows laptop", "desktop": "Desktop PC",
}


def protocol(category: str, *texts) -> dict:
    """{platform, label, steps:[{title, how, bad_if, url?}]} for this device.
    Unknown categories get an empty step list, never a generic one."""
    platform = platform_for(category, *texts)
    return {"platform": platform, "label": _PLATFORM_LABEL.get(platform, "Device"),
            "steps": list(_BY_PLATFORM.get(platform, []))}


def seller_proof_requests(category: str, *texts) -> list:
    """What to ask the seller to photograph before meeting. Used by the proof
    round and by the chat assistant. Each: {label, what, why}."""
    platform = platform_for(category, *texts)
    common = [
        {"label": "IMEI screen", "what": "A screenshot of *#06# showing the IMEI, plus a photo of the IMEI on the box label.",
         "why": "Lets you check the NTA registration and that the box belongs to this phone."},
    ]
    if platform == "ios":
        return [
            {"label": "About screen", "what": "Screenshot of Settings › General › About showing Model Number, Serial Number and, if present, Parts and Service History.",
             "why": "Shows the region variant and any replaced parts."},
            {"label": "Battery Health", "what": "Screenshot of Settings › Battery › Battery Health & Charging.",
             "why": "The only place the real battery capacity is shown."},
            {"label": "Signed-out Apple ID", "what": "Screenshot of Settings with no Apple ID signed in at the top, or the seller's agreement to sign out when you meet.",
             "why": "An iPhone still tied to someone else's Apple ID is unusable."},
        ] + common
    if platform == "samsung":
        return [
            {"label": "About phone", "what": "Screenshot of Settings › About phone showing Model number and Serial.", "why": "Region variant and warranty checks."},
            {"label": "Battery status", "what": "Screenshot of Samsung Members › Phone diagnostics › Battery status.", "why": "Battery condition without a third-party app."},
            {"label": "Download mode", "what": "Photo of the Download mode screen (Warranty Void and KG STATE lines).", "why": "Reveals custom firmware or a financing lock."},
        ] + common
    if platform == "android":
        return [
            {"label": "About phone", "what": "Screenshot of Settings › About phone showing Model and Android version with the security update date.", "why": "Confirms the model and how old the software is."},
            {"label": "Battery", "what": "Screenshot of the battery health screen, or AccuBattery's estimated capacity.", "why": "Replacement cost if it is weak."},
        ] + common
    if platform == "mac":
        return [
            {"label": "System Report › Power", "what": "Screenshot showing Cycle Count and Condition.", "why": "Battery age."},
            {"label": "About This Mac", "what": "Screenshot showing the model, chip, memory and serial number.", "why": "Confirms the configuration the listing claims."},
            {"label": "Find My off", "what": "Screenshot of System Settings showing no Apple ID, or Find My Mac off.", "why": "Activation Lock makes the Mac unusable."},
        ]
    if platform == "windows":
        return [
            {"label": "Battery report", "what": "Screenshot of the powercfg /batteryreport page showing Design and Full Charge capacity.", "why": "Battery wear in numbers."},
            {"label": "System › About", "what": "Screenshot of Settings › System › About (CPU, RAM) and Task Manager › Performance › GPU.", "why": "Specs match the listing."},
            {"label": "Drive health", "what": "Screenshot of CrystalDiskInfo.", "why": "Drive wear and age."},
        ]
    if platform == "desktop":
        return [
            {"label": "Task Manager › Performance", "what": "Screenshots of CPU, Memory and GPU pages.", "why": "Parts match the listing."},
            {"label": "Temperatures under load", "what": "Screenshot of HWMonitor after ten minutes of a game or benchmark.", "why": "Cooling and thermal paste condition."},
        ]
    return []
