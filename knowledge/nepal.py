"""Nepal-specific facts the report links to. Kept in one place so a change of
portal address or carrier policy is a one-line edit."""

REVIEWED = "2026-10-24"

# Nepal Telecommunications Authority's Mobile Device Management System. A phone
# whose IMEI is not registered here can be blocked from Nepali networks.
NTA_MDMS_URL = "https://mdms.nta.gov.np/"
NTA_MDMS_NAME = "NTA MDMS"

NTA_STEP = {
    "title": "Check the IMEI with the NTA before paying",
    "how": ("Ask the seller for the IMEI (dial *#06# on the phone, or read it from Settings › General › About "
            "on an iPhone, Settings › About phone on Android). Enter it on the NTA MDMS portal."),
    "bad_if": ("The portal says the IMEI is not registered or is blacklisted. An unregistered grey-market "
               "phone can stop working on Nepali networks, and a blacklisted one is reported stolen or blocked."),
    "url": NTA_MDMS_URL,
}

IMEI_MATCH_STEP = {
    "title": "Match the IMEI in three places",
    "how": "Compare the IMEI from *#06# with the one on the box label and on the SIM tray or back of the device.",
    "bad_if": "Any of the three differ. The box is not for this phone, or parts have been swapped.",
}

SIM_STEP = {
    "title": "Put your own SIM in",
    "how": "Insert your Nepal Telecom or Ncell SIM, make a call and open a web page on mobile data.",
    "bad_if": "No signal, 'SIM not supported', or an activation screen asking for a previous owner's account.",
}

ESIM_CARRIER_NOTE = ("Nepal Telecom and Ncell both issue eSIMs, but not on every plan and not at every outlet. "
                     "If the phone is eSIM-only, confirm with your carrier that your number can move to it "
                     "before you pay.")
