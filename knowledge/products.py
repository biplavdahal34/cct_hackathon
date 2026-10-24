"""Product lines the price pages know about: what each line is, which models exist in it, and what to
check on a used one. The pages themselves are built from the listings BEAT has checked, so a line only
appears once at least one listing for it has been analysed. A checked listing that matches no line here
still gets a page of its own, with a plain outline and no description.

Hand-maintained, like the other tables in this package. The text below was written from general
knowledge and has NOT been checked against the manufacturers' specification sheets, so the pages
say so (STATUS). A person should check each entry and then set REVIEWED to the date they did, and
STATUS to "reviewed". Models launched in 2025 or later are not in this draft: add them to
`generations`, newest last.

PICTURES. Official product images from the manufacturers, saved in static/products/ under a line id or
a generation id, in webp, jpg, png or avif, for example static/products/lenovo-loq.png. A line's card shows
the newest generation's picture, then the line's own. Without a file the pages draw a plain outline. List
where each picture came from in IMAGE_CREDITS: the page shows that credit under the picture. Never use
sellers' listing photos: these pages are public. The pictures are the manufacturers' property, so
using them on a public site is a decision for whoever runs it.

HOW LISTINGS ARE MATCHED. `match` is a regular expression run on a listing's title (lower case).
`number` finds the model number inside it. `sql` are loose LIKE patterns that narrow the database
query before the regular expressions decide. `queries` are the searches the daily crawler runs.
"""

STATUS = "draft"
REVIEWED = None            # date a person last checked this file, for example "2026-11-02"

IMAGE_CREDITS = {
    "lenovo-loq": {"owner": "Lenovo", "source": "https://www.lenovo.com/us/en/c/laptops/loq-laptops/",
                   "file": "https://p1-ofp.static.pub/ShareResource/ww/landing-pages/loq-brand-page/mar-2026/lenovo-loq-brand-page-loq-15-series.png"},
    "samsung-galaxy-s20-fe": {"owner": "Samsung", "source": "https://www.samsung.com/ie/business/smartphones/galaxy-s/galaxy-s20-fe-sm-g781-5g-sm-g781bzrdeua/",
                              "file": "https://images.samsung.com/is/image/samsung/ie-galaxy-s20-fe-am-g781-5g-sm-g781bzrdeua-frontcloudred-thumb-304622528"},
}

DRAFT_NOTE = ("Draft. This description was written from general knowledge and has not been checked against "
              "the manufacturer's specification sheets yet, and it may not list the newest models. "
              "The prices on these pages come from listings, not from this text.")

LINES = {
    "acer-nitro-v": {
        "name": "Acer Nitro V",
        "brand": "Acer",
        "kind": "gaming",
        "kind_label": "Gaming laptop",
        "tagline": "Acer's value gaming laptop: a recent H-series processor and an RTX graphics card in a plastic body.",
        "queries": ["Acer Nitro V"],
        "sql": ["%nitro%", "%anv1%"],
        "match": r"\bnitro[\s-]*v(?![a-z])|\banv\d{2}-\d{2}",
        "number": r"\banv\d{2}-\d{2}",
        "what_it_is": [
            "The Nitro V is Acer's budget gaming line, introduced in 2023. It gives you a recent Intel or AMD "
            "H-series processor and an NVIDIA RTX graphics card at a price well below Acer's Predator range. "
            "The savings come from the body, the screen and the cooling, which are plainer than on dearer gaming laptops.",
            "It comes in 15.6 inch and 16 inch sizes. Inside one model number the value changes a lot with the "
            "graphics card, the processor, the memory and the storage, so two listings with the same name can be "
            "worth quite different amounts. That is why the exact model matters more than the line name.",
        ],
        "good_for": [
            "Students and first-time buyers who want to play popular games at Full HD on a budget",
            "Video editing, design and 3D work that can use the graphics card",
            "Anyone who wants a recent processor and card without paying for a premium body",
        ],
        "not_for": [
            "Anyone who wants a thin, light laptop or a long battery life",
            "Quiet rooms: it gets loud when the fans work hard",
            "Buyers who need the best screen or build quality in this class",
        ],
        "generations": [
            {"id": "anv15-51", "year": 2023, "label": "Nitro V 15", "model_numbers": ["ANV15-51"],
             "cpu": "Intel Core i5-13420H or Core i7-13620H", "gpu": "NVIDIA RTX 2050, 3050, 4050 or 4060",
             "screen": "15.6 inch Full HD, 144 Hz"},
            {"id": "anv15-41", "year": 2023, "label": "Nitro V 15 (AMD)", "model_numbers": ["ANV15-41"],
             "cpu": "AMD Ryzen 5 7535HS or Ryzen 7 7735HS", "gpu": "NVIDIA RTX 2050, 3050, 4050 or 4060",
             "screen": "15.6 inch Full HD, 144 Hz"},
            {"id": "anv16-41", "year": 2024, "label": "Nitro V 16", "model_numbers": ["ANV16-41"],
             "cpu": "AMD Ryzen 5 8645HS or Ryzen 7 8845HS", "gpu": "NVIDIA RTX 4050 or 4060",
             "screen": "16 inch WUXGA, 165 Hz"},
        ],
        "check": [
            "Play a game or run a graphics test for ten minutes. The fans will rise but should stay steady. A laptop that shuts down or slows to a crawl has a cooling problem.",
            "Open Device Manager and confirm the graphics card is the one in the listing, for example an RTX 4050 and not an RTX 2050.",
            "Look at the screen on a plain white and a plain black page for dead pixels and light leaking at the edges.",
            "Press every key and check the keyboard backlight. Gaming laptops are used hard.",
            "Look at the hinges and the plastic around them. Cracks near the hinges are the usual damage on this class of laptop.",
            "Run powercfg /batteryreport in a command window and compare the design capacity with the full-charge capacity.",
            "Ask for the bill and the box, and check the serial number against the warranty on Acer's support site.",
        ],
        "weak_points": [
            "It runs hot and loud under load, as most budget gaming laptops do.",
            "The plastic body and the hinges are the usual weak points after a year or two of heavy use.",
            "The RTX 2050 versions are much weaker than the 4050 and 4060 versions, so check which card it has.",
        ],
        "price_notes": [
            "Price depends mostly on the graphics card, the memory and storage, and whether any warranty is left.",
            "A listing that does not name the graphics card cannot be priced properly. Ask for it.",
        ],
    },

    "acer-aspire-3": {
        "name": "Acer Aspire 3",
        "brand": "Acer",
        "kind": "everyday",
        "kind_label": "Everyday laptop",
        "tagline": "Acer's budget laptop for study, office work and browsing. No gaming graphics card.",
        "queries": ["Acer Aspire 3"],
        "sql": ["%aspire%", "%a315%", "%a314%"],
        "match": r"\baspire[\s-]*3\b|\ba31[45]-\d{2}",
        "number": r"\ba31[45]-\d{2}[a-z]?",
        "what_it_is": [
            "The Aspire 3 is one of Acer's cheapest laptops and one of the most common second-hand laptops in "
            "Nepal. It is made for browsing, documents, video calls and study. It uses the processor's built-in "
            "graphics, so it is not a gaming or video-editing machine.",
            "It is sold in 14 inch and 15.6 inch sizes, with model numbers starting A314 and A315. Over the years "
            "Acer has used Intel and AMD processors in it, so the processor generation matters when you compare prices.",
        ],
        "good_for": [
            "Study, office work, browsing and video calls",
            "Buyers who want the lowest price and do not need graphics power",
        ],
        "not_for": [
            "Gaming, 3D work or heavy video editing",
            "Anyone who needs a premium screen, keyboard or build",
        ],
        "generations": [
            {"id": "a315-58", "year": 2021, "label": "Aspire 3 15", "model_numbers": ["A315-58"],
             "cpu": "Intel Core i3-1115G4 or Core i5-1135G7, 11th generation", "gpu": "Intel built-in graphics",
             "screen": "15.6 inch Full HD"},
            {"id": "a315-59", "year": 2022, "label": "Aspire 3 15", "model_numbers": ["A315-59"],
             "cpu": "Intel Core i3-1215U or Core i5-1235U, 12th generation", "gpu": "Intel built-in graphics",
             "screen": "15.6 inch Full HD"},
            {"id": "a315-24p", "year": 2023, "label": "Aspire 3 15 (AMD)", "model_numbers": ["A315-24P"],
             "cpu": "AMD Ryzen 3 7320U or Ryzen 5 7520U", "gpu": "AMD built-in graphics",
             "screen": "15.6 inch Full HD"},
        ],
        "check": [
            "Check whether the storage is an SSD or a hard drive in Task Manager. A hard drive makes the laptop feel slow.",
            "Check how much memory it has. Eight gigabytes is comfortable today, four is not.",
            "Look at the screen on a plain white and a plain black page for dead pixels and marks.",
            "Press every key and test the trackpad, including both buttons.",
            "Check the hinges and the plastic around them for cracks.",
            "Run powercfg /batteryreport in a command window and compare the design capacity with the full-charge capacity.",
            "Ask for the bill and check the serial number against the warranty on Acer's support site.",
        ],
        "weak_points": [
            "The body is plain plastic and the screen is basic, which is how the price is kept low.",
            "Older ones with a hard drive are slow, and the battery on an older one is often worn.",
        ],
        "price_notes": [
            "Price depends mostly on the processor generation, the memory, whether the storage is an SSD, and the battery.",
            "A listing that does not name the processor cannot be priced properly. Ask for it.",
        ],
    },

    "lenovo-loq": {
        "name": "Lenovo LOQ",
        "brand": "Lenovo",
        "kind": "gaming",
        "kind_label": "Gaming laptop",
        "tagline": "Lenovo's value gaming laptop, priced below its Legion range.",
        "queries": ["Lenovo LOQ"],
        "sql": ["%loq%"],
        "match": r"\bloq\b",
        "number": r"\b1[56][a-z]{3}\d\b",
        "what_it_is": [
            "LOQ is Lenovo's budget gaming line, introduced in 2023. It pairs a recent H-series processor with an RTX "
            "graphics card at a lower price than the Legion range, in a plainer body. It is sold in 15 inch and 16 inch "
            "sizes with Intel and AMD processors.",
            "The model number tells you the generation: 15IRH8 and 15APH8 are 2023 models, 15IAX9 and 15ARP9 are 2024 models. "
            "Within one model number the price changes with the graphics card, the memory and the storage.",
        ],
        "generations": [
            {"id": "15irh8", "year": 2023, "label": "LOQ 15 (Intel)", "model_numbers": ["15IRH8"],
             "cpu": "Intel Core i5-13420H or Core i7-13620H", "gpu": "NVIDIA RTX 3050, 4050 or 4060", "screen": "15.6 inch Full HD, 144 Hz"},
            {"id": "15aph8", "year": 2023, "label": "LOQ 15 (AMD)", "model_numbers": ["15APH8"],
             "cpu": "AMD Ryzen 5 7640HS or Ryzen 7 7840HS", "gpu": "NVIDIA RTX 3050, 4050 or 4060", "screen": "15.6 inch Full HD, 144 Hz"},
            {"id": "15iax9", "year": 2024, "label": "LOQ 15 (Intel)", "model_numbers": ["15IAX9"],
             "cpu": "Intel Core i5-12450HX", "gpu": "NVIDIA RTX 3050, 4050 or 4060", "screen": "15.6 inch Full HD, 144 Hz"},
            {"id": "15arp9", "year": 2024, "label": "LOQ 15 (AMD)", "model_numbers": ["15ARP9"],
             "cpu": "AMD Ryzen 5 7235HS or Ryzen 7 7435HS", "gpu": "NVIDIA RTX 3050, 4050 or 4060", "screen": "15.6 inch Full HD, 144 Hz"},
        ],
    },

    "samsung-galaxy-s20-fe": {
        "name": "Samsung Galaxy S20 FE",
        "brand": "Samsung",
        "kind": "phone",
        "kind_label": "Phone",
        "tagline": "Samsung's 2020 \"Fan Edition\" of the Galaxy S20.",
        "queries": ["Samsung S20 FE"],
        "sql": ["%s20%fe%", "%s20 fan%"],
        "match": r"\bs20[\s-]*fe\b|\bsm-g78[01]",
        "number": r"\bsm-g78[01][a-z0-9]*",
        "what_it_is": [
            "The Galaxy S20 FE came out in late 2020 as a cheaper version of the S20 with most of the same features: a 6.5 inch "
            "AMOLED screen with a 120 Hz refresh rate, a triple rear camera and a plastic back.",
            "It was sold in 4G and 5G versions with different processors. The model number on the box or in Settings, "
            "SM-G780 for 4G and SM-G781 for 5G, tells you which one it is.",
        ],
        "generations": [
            {"id": "g781", "year": 2020, "label": "Galaxy S20 FE 5G", "model_numbers": ["SM-G781"],
             "cpu": "Snapdragon 865 or Exynos 990, by region", "gpu": "", "screen": "6.5 inch AMOLED, 120 Hz"},
            {"id": "g780", "year": 2020, "label": "Galaxy S20 FE 4G", "model_numbers": ["SM-G780"],
             "cpu": "Exynos 990 or Snapdragon 865, by region", "gpu": "", "screen": "6.5 inch AMOLED, 120 Hz"},
        ],
    },

    "samsung-galaxy-s22-ultra": {
        "name": "Samsung Galaxy S22 Ultra",
        "brand": "Samsung",
        "kind": "phone",
        "kind_label": "Phone",
        "tagline": "Samsung's 2022 top model, with a built-in S Pen.",
        "queries": ["Samsung S22 Ultra"],
        "sql": ["%s22%ultra%", "%s908%"],
        "match": r"\bs22[\s-]*ultra\b|\bsm-s908",
        "number": r"\bsm-s908[a-z0-9]*",
        "what_it_is": [
            "The Galaxy S22 Ultra came out in February 2022 with a 6.8 inch AMOLED screen, a built-in S Pen and a "
            "four-camera back. It sits at the top of the S22 range.",
        ],
        "generations": [
            {"id": "s908", "year": 2022, "label": "Galaxy S22 Ultra", "model_numbers": ["SM-S908"],
             "cpu": "Snapdragon 8 Gen 1 or Exynos 2200, by region", "gpu": "", "screen": "6.8 inch AMOLED, 120 Hz"},
        ],
    },
}
