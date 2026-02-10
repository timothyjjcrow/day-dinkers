"""Seed the database with VERIFIED Humboldt County pickleball courts.
Sources: playtimescheduler.com, pickleballarcata.com, bounce.game, cityofarcata.org
"""
from backend.app import db
from backend.models import Court

# Only verified courts with real addresses and accurate coordinates
HUMBOLDT_COURTS = [
    # ── Eureka ───────────────────────────────────────────────────
    {
        "name": "Adorni Center",
        "description": "City of Eureka recreation center on the waterfront. "
                       "Indoor gymnasium hosts regular pickleball open play sessions. "
                       "Popular with local players of all skill levels.",
        "address": "1011 Waterfront Dr", "city": "Eureka", "zip_code": "95501",
        "latitude": 40.806670, "longitude": -124.158736,
        "indoor": True, "lighted": True, "num_courts": 4,
        "surface_type": "Wood (gym)", "court_type": "shared",
        "hours": "Mon-Fri 8am-9pm, Sat 9am-5pm, Sun 10am-4pm",
        "open_play_schedule": "Check City of Eureka Parks & Rec for current schedule",
        "fees": "Drop-in fee",
        "phone": "(707) 441-4018",
        "website": "https://www.ci.eureka.ca.gov",
        "has_restrooms": True, "has_parking": True, "has_water": True,
        "wheelchair_accessible": True, "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    {
        "name": "College of the Redwoods",
        "description": "Community college campus with outdoor pickleball courts. "
                       "Free to play, open to the public. Eight courts available — "
                       "one of the largest pickleball facilities in Humboldt County.",
        "address": "7351 Tompkins Hill Rd", "city": "Eureka", "zip_code": "95501",
        "latitude": 40.6978, "longitude": -124.1945,
        "indoor": False, "lighted": False, "num_courts": 8,
        "surface_type": "Asphalt", "court_type": "dedicated",
        "hours": "Dawn to dusk",
        "fees": "Free",
        "website": "https://www.redwoods.edu",
        "has_restrooms": True, "has_parking": True, "has_water": False,
        "wheelchair_accessible": True, "nets_provided": False,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    {
        "name": "Highland Park",
        "description": "Large outdoor pickleball facility in southeast Eureka "
                       "near the Sequoia Park area. Eight courts make this one of "
                       "the biggest outdoor venues in the county. Free public access.",
        "address": "1206 Highland Ave", "city": "Eureka", "zip_code": "95503",
        "latitude": 40.775769, "longitude": -124.183079,
        "indoor": False, "lighted": False, "num_courts": 8,
        "surface_type": "Asphalt", "court_type": "dedicated",
        "hours": "Dawn to dusk",
        "fees": "Free",
        "has_restrooms": False, "has_parking": True, "has_water": False,
        "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    # ── Arcata ───────────────────────────────────────────────────
    {
        "name": "Arcata Community Center",
        "description": "City-run community center in the heart of Arcata. Indoor "
                       "gymnasium hosts regular pickleball sessions. Very active "
                       "local community. Beginner-friendly.",
        "address": "321 Dr Martin Luther King Jr Pkwy", "city": "Arcata", "zip_code": "95521",
        "latitude": 40.864435, "longitude": -124.080418,
        "indoor": True, "lighted": True, "num_courts": 3,
        "surface_type": "Wood (gym)", "court_type": "shared",
        "hours": "Mon-Fri 8am-9pm, Sat 9am-5pm",
        "open_play_schedule": "Check City of Arcata Recreation for current schedule",
        "fees": "Drop-in fee",
        "phone": "(707) 822-7091",
        "website": "https://www.cityofarcata.org/recreation",
        "has_restrooms": True, "has_parking": True, "has_water": True,
        "wheelchair_accessible": True, "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    {
        "name": "Cal Poly Humboldt Forbes Complex (East Gym)",
        "description": "University gymnasium with indoor pickleball courts. Open to "
                       "community members during designated hours. Modern facility "
                       "on the Cal Poly Humboldt campus.",
        "address": "1 Harpst St (Union St entrance)", "city": "Arcata", "zip_code": "95521",
        "latitude": 40.875575, "longitude": -124.078044,
        "indoor": True, "lighted": True, "num_courts": 4,
        "surface_type": "Wood (gym)", "court_type": "shared",
        "hours": "Check university rec schedule",
        "fees": "Free for students / Community day pass",
        "phone": "(707) 826-6011",
        "website": "https://recreation.humboldt.edu",
        "has_restrooms": True, "has_parking": True, "has_water": True,
        "wheelchair_accessible": True, "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    {
        "name": "Sunny Brae Park",
        "description": "Outdoor dedicated pickleball courts in the Sunny Brae "
                       "neighborhood of south Arcata. Permanent nets installed. "
                       "Free public courts in a beautiful park setting.",
        "address": "Sunny Brae Park", "city": "Arcata", "zip_code": "95521",
        "latitude": 40.860170, "longitude": -124.066668,
        "indoor": False, "lighted": False, "num_courts": 3,
        "surface_type": "Asphalt", "court_type": "dedicated",
        "hours": "Dawn to dusk",
        "fees": "Free",
        "has_restrooms": False, "has_parking": True, "has_water": False,
        "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    {
        "name": "Carlson Park",
        "description": "Outdoor dedicated pickleball courts in north Arcata off "
                       "Giuntoli Lane, next to McIntosh Farm Country Store. "
                       "Free public courts in a quiet neighborhood park setting.",
        "address": "Giuntoli Ln", "city": "Arcata", "zip_code": "95521",
        "latitude": 40.907769, "longitude": -124.078886,
        "indoor": False, "lighted": False, "num_courts": 2,
        "surface_type": "Asphalt", "court_type": "dedicated",
        "hours": "Dawn to dusk",
        "fees": "Free",
        "has_restrooms": False, "has_parking": True, "has_water": False,
        "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    {
        "name": "Larson Park",
        "description": "Outdoor lighted pickleball courts in the Westwood neighborhood "
                       "of Arcata. Nets provided. Lights allow evening play. "
                       "Popular spot for after-work games.",
        "address": "901 Grant Ave", "city": "Arcata", "zip_code": "95521",
        "latitude": 40.881469, "longitude": -124.085657,
        "indoor": False, "lighted": True, "num_courts": 3,
        "surface_type": "Asphalt", "court_type": "dedicated",
        "hours": "Dawn to 10pm (lights)",
        "fees": "Free",
        "has_restrooms": False, "has_parking": True, "has_water": False,
        "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
    # ── Nearby Communities ───────────────────────────────────────
    {
        "name": "McKinleyville Activity Center",
        "description": "Community activity center with indoor pickleball courts. "
                       "McKinleyville's main recreation hub with a growing "
                       "pickleball community.",
        "address": "1705 Gwin Rd", "city": "McKinleyville", "zip_code": "95519",
        "latitude": 40.942414, "longitude": -124.097863,
        "indoor": True, "lighted": True, "num_courts": 3,
        "surface_type": "Wood (gym)", "court_type": "shared",
        "hours": "Check McKinleyville CSD for schedule",
        "fees": "Drop-in fee",
        "phone": "(707) 839-9003",
        "has_restrooms": True, "has_parking": True, "has_water": True,
        "wheelchair_accessible": True, "nets_provided": True,
        "skill_levels": "beginner,intermediate", "verified": True,
    },
    {
        "name": "Blue Lake Roller Rink",
        "description": "Indoor roller rink converted for pickleball play. "
                       "Three courts available during designated pickleball hours. "
                       "Unique venue in the small town of Blue Lake.",
        "address": "312 South Railroad Ave", "city": "Blue Lake", "zip_code": "95525",
        "latitude": 40.882089, "longitude": -123.992080,
        "indoor": True, "lighted": True, "num_courts": 3,
        "surface_type": "Concrete", "court_type": "shared",
        "hours": "Check schedule for pickleball hours",
        "fees": "Drop-in fee",
        "has_restrooms": True, "has_parking": True,
        "nets_provided": True,
        "skill_levels": "beginner,intermediate", "verified": True,
    },
    {
        "name": "Bear River Recreation Center",
        "description": "Indoor recreation center in Loleta with four pickleball "
                       "courts. South of Eureka along the 101 corridor.",
        "address": "11 Singley Hill Rd", "city": "Loleta", "zip_code": "95551",
        "latitude": 40.627304, "longitude": -124.208353,
        "indoor": True, "lighted": True, "num_courts": 4,
        "surface_type": "Concrete", "court_type": "shared",
        "hours": "Check schedule",
        "fees": "Drop-in fee",
        "has_restrooms": True, "has_parking": True, "has_water": True,
        "nets_provided": True,
        "skill_levels": "beginner,intermediate,advanced", "verified": True,
    },
]


def seed_courts():
    """Insert verified Humboldt County courts if the database is empty."""
    if Court.query.first():
        return 0

    count = 0
    for court_data in HUMBOLDT_COURTS:
        court_data['state'] = 'CA'
        court = Court(**court_data)
        db.session.add(court)
        count += 1

    db.session.commit()
    return count
