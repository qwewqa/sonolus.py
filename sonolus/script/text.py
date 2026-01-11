from enum import StrEnum


class StandardText(StrEnum):
    """Standard text constants."""

    CUSTOM_SERVER = "#CUSTOM_SERVER"
    """Custom Server"""

    COLLECTION = "#COLLECTION"
    """Collection"""

    SERVER = "#SERVER"
    """Server"""

    ADDRESS = "#ADDRESS"
    """Address"""

    EXPIRATION = "#EXPIRATION"
    """Expiration"""

    STORAGE = "#STORAGE"
    """Storage"""

    LOG = "#LOG"
    """Log"""

    INQUIRY = "#INQUIRY"
    """Inquiry"""

    BANNER = "#BANNER"
    """Banner"""

    POST = "#POST"
    """Post"""

    PLAYLIST = "#PLAYLIST"
    """Playlist"""

    LEVEL = "#LEVEL"
    """Level"""

    SKIN = "#SKIN"
    """Skin"""

    BACKGROUND = "#BACKGROUND"
    """Background"""

    EFFECT = "#EFFECT"
    """SFX"""

    PARTICLE = "#PARTICLE"
    """Particle"""

    ENGINE = "#ENGINE"
    """Engine"""

    REPLAY = "#REPLAY"
    """Replay"""

    USER = "#USER"
    """User"""

    ROOM = "#ROOM"
    """Room"""

    POST_THUMBNAIL = "#POST_THUMBNAIL"
    """Thumbnail"""

    PLAYLIST_THUMBNAIL = "#PLAYLIST_THUMBNAIL"
    """Thumbnail"""

    LEVEL_COVER = "#LEVEL_COVER"
    """Cover"""

    LEVEL_BGM = "#LEVEL_BGM"
    """BGM"""

    LEVEL_PREVIEW = "#LEVEL_PREVIEW"
    """Preview"""

    LEVEL_DATA = "#LEVEL_DATA"
    """Data"""

    SKIN_THUMBNAIL = "#SKIN_THUMBNAIL"
    """Thumbnail"""

    SKIN_DATA = "#SKIN_DATA"
    """Data"""

    SKIN_TEXTURE = "#SKIN_TEXTURE"
    """Texture"""

    BACKGROUND_THUMBNAIL = "#BACKGROUND_THUMBNAIL"
    """Thumbnail"""

    BACKGROUND_IMAGE = "#BACKGROUND_IMAGE"
    """Image"""

    BACKGROUND_DATA = "#BACKGROUND_DATA"
    """Data"""

    BACKGROUND_CONFIGURATION = "#BACKGROUND_CONFIGURATION"
    """Configuration"""

    EFFECT_THUMBNAIL = "#EFFECT_THUMBNAIL"
    """Thumbnail"""

    EFFECT_DATA = "#EFFECT_DATA"
    """Data"""

    EFFECT_AUDIO = "#EFFECT_AUDIO"
    """Audio"""

    PARTICLE_THUMBNAIL = "#PARTICLE_THUMBNAIL"
    """Thumbnail"""

    PARTICLE_DATA = "#PARTICLE_DATA"
    """Data"""

    PARTICLE_TEXTURE = "#PARTICLE_TEXTURE"
    """Texture"""

    ENGINE_THUMBNAIL = "#ENGINE_THUMBNAIL"
    """Thumbnail"""

    ENGINE_PLAYDATA = "#ENGINE_PLAYDATA"
    """Play Data"""

    ENGINE_WATCHDATA = "#ENGINE_WATCHDATA"
    """Watch Data"""

    ENGINE_PREVIEWDATA = "#ENGINE_PREVIEWDATA"
    """Preview Data"""

    ENGINE_TUTORIALDATA = "#ENGINE_TUTORIALDATA"
    """Tutorial Data"""

    ENGINE_ROM = "#ENGINE_ROM"
    """ROM"""

    ENGINE_CONFIGURATION = "#ENGINE_CONFIGURATION"
    """Configuration"""

    REPLAY_DATA = "#REPLAY_DATA"
    """Data"""

    REPLAY_CONFIGURATION = "#REPLAY_CONFIGURATION"
    """Configuration"""

    ROOM_COVER = "#ROOM_COVER"
    """Cover"""

    ROOM_BGM = "#ROOM_BGM"
    """BGM"""

    ROOM_PREVIEW = "#ROOM_PREVIEW"
    """Preview"""

    GRADE = "#GRADE"
    """Grade"""

    ARCADE_SCORE = "#ARCADE_SCORE"
    """Arcade Score"""

    ACCURACY_SCORE = "#ACCURACY_SCORE"
    """Accuracy Score"""

    COMBO = "#COMBO"
    """Combo"""

    PERFECT = "#PERFECT"
    """Perfect"""

    GREAT = "#GREAT"
    """Great"""

    GOOD = "#GOOD"
    """Good"""

    MISS = "#MISS"
    """Miss"""

    JUDGMENT = "#JUDGMENT"
    """Judgment"""

    ACCURACY = "#ACCURACY"
    """Accuracy"""

    FILTER = "#FILTER"
    """Filter"""

    SORT = "#SORT"
    """Sort"""

    KEYWORDS = "#KEYWORDS"
    """Keywords"""

    NAME = "#NAME"
    """Name"""

    RATING = "#RATING"
    """Rating"""

    RATING_MINIMUM = "#RATING_MINIMUM"
    """Minimum Rating"""

    RATING_MAXIMUM = "#RATING_MAXIMUM"
    """Maximum Rating"""

    TITLE = "#TITLE"
    """Title"""

    SUBTITLE = "#SUBTITLE"
    """Subtitle"""

    ARTISTS = "#ARTISTS"
    """Artists"""

    TIME = "#TIME"
    """Time"""

    AUTHOR = "#AUTHOR"
    """Author"""

    COAUTHOR = "#COAUTHOR"
    """Coauthor"""

    DESCRIPTION = "#DESCRIPTION"
    """Description"""

    GENRE = "#GENRE"
    """Genre"""

    TYPE = "#TYPE"
    """Type"""

    CATEGORY = "#CATEGORY"
    """Category"""

    STATUS = "#STATUS"
    """Status"""

    LANGUAGE = "#LANGUAGE"
    """Language"""

    DIFFICULTY = "#DIFFICULTY"
    """Difficulty"""

    VERSION = "#VERSION"
    """Version"""

    LENGTH = "#LENGTH"
    """Length"""

    LENGTH_MINIMUM = "#LENGTH_MINIMUM"
    """Minimum Length"""

    LENGTH_MAXIMUM = "#LENGTH_MAXIMUM"
    """Maximum Length"""

    ADDITIONAL_INFORMATION = "#ADDITIONAL_INFORMATION"
    """Additional Information"""

    TIMEZONE = "#TIMEZONE"
    """Timezone"""

    REGION = "#REGION"
    """Region"""

    TAG = "#TAG"
    """Tag"""

    INCLUDE_TAG = "#INCLUDE_TAG"
    """Include Tag"""

    EXCLUDE_TAG = "#EXCLUDE_TAG"
    """Exclude Tag"""

    CONTENT = "#CONTENT"
    """Content"""

    COMMENT = "#COMMENT"
    """Comment"""

    MESSAGE = "#MESSAGE"
    """Message"""

    NOTIFICATION = "#NOTIFICATION"
    """Notification"""

    ROLE = "#ROLE"
    """Role"""

    PERMISSION = "#PERMISSION"
    """Permission"""

    SPEED = "#SPEED"
    """Level Speed"""

    MIRROR = "#MIRROR"
    """Mirror Level"""

    RANDOM = "#RANDOM"
    """Random"""

    HIDDEN = "#HIDDEN"
    """Hidden"""

    JUDGMENT_STRICT = "#JUDGMENT_STRICT"
    """Strict Judgment"""

    JUDGMENT_LOOSE = "#JUDGMENT_LOOSE"
    """Loose Judgment"""

    EFFECT_AUTO = "#EFFECT_AUTO"
    """Auto SFX"""

    HAPTIC = "#HAPTIC"
    """Haptic"""

    STAGE = "#STAGE"
    """Stage"""

    STAGE_POSITION = "#STAGE_POSITION"
    """Stage Position"""

    STAGE_SIZE = "#STAGE_SIZE"
    """Stage Size"""

    STAGE_ROTATION = "#STAGE_ROTATION"
    """Stage Rotation"""

    STAGE_DIRECTION = "#STAGE_DIRECTION"
    """Stage Direction"""

    STAGE_ALPHA = "#STAGE_ALPHA"
    """Stage Transparency"""

    STAGE_ANIMATION = "#STAGE_ANIMATION"
    """Stage Animation"""

    STAGE_TILT = "#STAGE_TILT"
    """Stage Tilt"""

    STAGE_COVER_VERTICAL = "#STAGE_COVER_VERTICAL"
    """Vertical Stage Cover"""

    STAGE_COVER_HORIZONTAL = "#STAGE_COVER_HORIZONTAL"
    """Horizontal Stage Cover"""

    STAGE_COVER_ALPHA = "#STAGE_COVER_ALPHA"
    """Stage Cover Transparency"""

    STAGE_ASPECTRATIO_LOCK = "#STAGE_ASPECTRATIO_LOCK"
    """Lock Stage Aspect Ratio"""

    STAGE_EFFECT = "#STAGE_EFFECT"
    """Stage Effect"""

    STAGE_EFFECT_POSITION = "#STAGE_EFFECT_POSITION"
    """Stage Effect Position"""

    STAGE_EFFECT_SIZE = "#STAGE_EFFECT_SIZE"
    """Stage Effect Size"""

    STAGE_EFFECT_ROTATION = "#STAGE_EFFECT_ROTATION"
    """Stage Effect Rotation"""

    STAGE_EFFECT_DIRECTION = "#STAGE_EFFECT_DIRECTION"
    """Stage Effect Direction"""

    STAGE_EFFECT_ALPHA = "#STAGE_EFFECT_ALPHA"
    """Stage Effect Transparency"""

    LANE = "#LANE"
    """Lane"""

    LANE_POSITION = "#LANE_POSITION"
    """Lane Position"""

    LANE_SIZE = "#LANE_SIZE"
    """Lane Size"""

    LANE_ROTATION = "#LANE_ROTATION"
    """Lane Rotation"""

    LANE_DIRECTION = "#LANE_DIRECTION"
    """Lane Direction"""

    LANE_ALPHA = "#LANE_ALPHA"
    """Lane Transparency"""

    LANE_ANIMATION = "#LANE_ANIMATION"
    """Lane Animation"""

    LANE_EFFECT = "#LANE_EFFECT"
    """Lane Effect"""

    LANE_EFFECT_POSITION = "#LANE_EFFECT_POSITION"
    """Lane Effect Position"""

    LANE_EFFECT_SIZE = "#LANE_EFFECT_SIZE"
    """Lane Effect Size"""

    LANE_EFFECT_ROTATION = "#LANE_EFFECT_ROTATION"
    """Lane Effect Rotation"""

    LANE_EFFECT_DIRECTION = "#LANE_EFFECT_DIRECTION"
    """Lane Effect Direction"""

    LANE_EFFECT_ALPHA = "#LANE_EFFECT_ALPHA"
    """Lane Effect Transparency"""

    JUDGELINE = "#JUDGELINE"
    """Judgment Line"""

    JUDGELINE_POSITION = "#JUDGELINE_POSITION"
    """Judgment Line Position"""

    JUDGELINE_SIZE = "#JUDGELINE_SIZE"
    """Judgment Line Size"""

    JUDGELINE_ROTATION = "#JUDGELINE_ROTATION"
    """Judgment Line Rotation"""

    JUDGELINE_DIRECTION = "#JUDGELINE_DIRECTION"
    """Judgment Line Direction"""

    JUDGELINE_ALPHA = "#JUDGELINE_ALPHA"
    """Judgment Line Transparency"""

    JUDGELINE_ANIMATION = "#JUDGELINE_ANIMATION"
    """Judgment Line Animation"""

    JUDGELINE_EFFECT = "#JUDGELINE_EFFECT"
    """Judgment Line Effect"""

    JUDGELINE_EFFECT_POSITION = "#JUDGELINE_EFFECT_POSITION"
    """Judgment Line Effect Position"""

    JUDGELINE_EFFECT_SIZE = "#JUDGELINE_EFFECT_SIZE"
    """Judgment Line Effect Size"""

    JUDGELINE_EFFECT_ROTATION = "#JUDGELINE_EFFECT_ROTATION"
    """Judgment Line Effect Rotation"""

    JUDGELINE_EFFECT_DIRECTION = "#JUDGELINE_EFFECT_DIRECTION"
    """Judgment Line Effect Direction"""

    JUDGELINE_EFFECT_ALPHA = "#JUDGELINE_EFFECT_ALPHA"
    """Judgment Line Effect Transparency"""

    SLOT = "#SLOT"
    """Slot"""

    SLOT_POSITION = "#SLOT_POSITION"
    """Slot Position"""

    SLOT_SIZE = "#SLOT_SIZE"
    """Slot Size"""

    SLOT_ROTATION = "#SLOT_ROTATION"
    """Slot Rotation"""

    SLOT_DIRECTION = "#SLOT_DIRECTION"
    """Slot Direction"""

    SLOT_ALPHA = "#SLOT_ALPHA"
    """Slot Transparency"""

    SLOT_ANIMATION = "#SLOT_ANIMATION"
    """Slot Animation"""

    SLOT_EFFECT = "#SLOT_EFFECT"
    """Slot Effect"""

    SLOT_EFFECT_POSITION = "#SLOT_EFFECT_POSITION"
    """Slot Effect Position"""

    SLOT_EFFECT_SIZE = "#SLOT_EFFECT_SIZE"
    """Slot Effect Size"""

    SLOT_EFFECT_ROTATION = "#SLOT_EFFECT_ROTATION"
    """Slot Effect Rotation"""

    SLOT_EFFECT_DIRECTION = "#SLOT_EFFECT_DIRECTION"
    """Slot Effect Direction"""

    SLOT_EFFECT_ALPHA = "#SLOT_EFFECT_ALPHA"
    """Slot Effect Transparency"""

    NOTE = "#NOTE"
    """Note"""

    NOTE_SPEED = "#NOTE_SPEED"
    """Note Speed"""

    NOTE_SPEED_RANDOM = "#NOTE_SPEED_RANDOM"
    """Random Note Speed"""

    NOTE_POSITION = "#NOTE_POSITION"
    """Note Position"""

    NOTE_SIZE = "#NOTE_SIZE"
    """Note Size"""

    NOTE_ROTATION = "#NOTE_ROTATION"
    """Note Rotation"""

    NOTE_DIRECTION = "#NOTE_DIRECTION"
    """Note Direction"""

    NOTE_COLOR = "#NOTE_COLOR"
    """Note Color"""

    NOTE_ALPHA = "#NOTE_ALPHA"
    """Note Transparency"""

    NOTE_ANIMATION = "#NOTE_ANIMATION"
    """Note Animation"""

    NOTE_EFFECT = "#NOTE_EFFECT"
    """Note Effect"""

    NOTE_EFFECT_POSITION = "#NOTE_EFFECT_POSITION"
    """Note Effect Position"""

    NOTE_EFFECT_SIZE = "#NOTE_EFFECT_SIZE"
    """Note Effect Size"""

    NOTE_EFFECT_ROTATION = "#NOTE_EFFECT_ROTATION"
    """Note Effect Rotation"""

    NOTE_EFFECT_DIRECTION = "#NOTE_EFFECT_DIRECTION"
    """Note Effect Direction"""

    NOTE_EFFECT_COLOR = "#NOTE_EFFECT_COLOR"
    """Note Effect Color"""

    NOTE_EFFECT_ALPHA = "#NOTE_EFFECT_ALPHA"
    """Note Effect Transparency"""

    MARKER = "#MARKER"
    """Marker"""

    MARKER_POSITION = "#MARKER_POSITION"
    """Marker Position"""

    MARKER_SIZE = "#MARKER_SIZE"
    """Marker Size"""

    MARKER_ROTATION = "#MARKER_ROTATION"
    """Marker Rotation"""

    MARKER_DIRECTION = "#MARKER_DIRECTION"
    """Marker Direction"""

    MARKER_COLOR = "#MARKER_COLOR"
    """Marker Color"""

    MARKER_ALPHA = "#MARKER_ALPHA"
    """Marker Transparency"""

    MARKER_ANIMATION = "#MARKER_ANIMATION"
    """Marker Animation"""

    CONNECTOR = "#CONNECTOR"
    """Connector"""

    CONNECTOR_POSITION = "#CONNECTOR_POSITION"
    """Connector Position"""

    CONNECTOR_SIZE = "#CONNECTOR_SIZE"
    """Connector Size"""

    CONNECTOR_ROTATION = "#CONNECTOR_ROTATION"
    """Connector Rotation"""

    CONNECTOR_DIRECTION = "#CONNECTOR_DIRECTION"
    """Connector Direction"""

    CONNECTOR_COLOR = "#CONNECTOR_COLOR"
    """Connector Color"""

    CONNECTOR_ALPHA = "#CONNECTOR_ALPHA"
    """Connector Transparency"""

    CONNECTOR_ANIMATION = "#CONNECTOR_ANIMATION"
    """Connector Animation"""

    SIMLINE = "#SIMLINE"
    """Simultaneous Line"""

    SIMLINE_POSITION = "#SIMLINE_POSITION"
    """Simultaneous Line Position"""

    SIMLINE_SIZE = "#SIMLINE_SIZE"
    """Simultaneous Line Size"""

    SIMLINE_ROTATION = "#SIMLINE_ROTATION"
    """Simultaneous Line Rotation"""

    SIMLINE_DIRECTION = "#SIMLINE_DIRECTION"
    """Simultaneous Line Direction"""

    SIMLINE_COLOR = "#SIMLINE_COLOR"
    """Simultaneous Line Color"""

    SIMLINE_ALPHA = "#SIMLINE_ALPHA"
    """Simultaneous Line Transparency"""

    SIMLINE_ANIMATION = "#SIMLINE_ANIMATION"
    """Simultaneous Line Animation"""

    PREVIEW_SCALE_VERTICAL = "#PREVIEW_SCALE_VERTICAL"
    """Preview Vertical Scale"""

    PREVIEW_SCALE_HORIZONTAL = "#PREVIEW_SCALE_HORIZONTAL"
    """Preview Horizontal Scale"""

    PREVIEW_TIME = "#PREVIEW_TIME"
    """Preview Time"""

    PREVIEW_SCORE = "#PREVIEW_SCORE"
    """Preview Score"""

    PREVIEW_BPM = "#PREVIEW_BPM"
    """Preview BPM"""

    PREVIEW_TIMESCALE = "#PREVIEW_TIMESCALE"
    """Preview Time Scale"""

    PREVIEW_BEAT = "#PREVIEW_BEAT"
    """Preview Beat"""

    PREVIEW_MEASURE = "#PREVIEW_MEASURE"
    """Preview Measure"""

    PREVIEW_COMBO = "#PREVIEW_COMBO"
    """Preview Combo"""

    UI = "#UI"
    """UI"""

    UI_METRIC = "#UI_METRIC"
    """UI Metric"""

    UI_PRIMARY_METRIC = "#UI_PRIMARY_METRIC"
    """UI Primary Metric"""

    UI_SECONDARY_METRIC = "#UI_SECONDARY_METRIC"
    """UI Secondary Metric"""

    UI_JUDGMENT = "#UI_JUDGMENT"
    """UI Judgment"""

    UI_COMBO = "#UI_Combo"
    """UI Combo"""

    UI_MENU = "#UI_Menu"
    """UI Menu"""

    ON = "#ON"
    """ON"""

    OFF = "#OFF"
    """OFF"""

    NONE = "#NONE"
    """None"""

    ANY = "#ANY"
    """Any"""

    ALL = "#ALL"
    """All"""

    OTHERS = "#OTHERS"
    """Others"""

    SHORT = "#SHORT"
    """Short"""

    LONG = "#LONG"
    """Long"""

    HIGH = "#HIGH"
    """High"""

    MID = "#MID"
    """Mid"""

    LOW = "#LOW"
    """Low"""

    SMALL = "#SMALL"
    """Small"""

    MEDIUM = "#MEDIUM"
    """Medium"""

    LARGE = "#LARGE"
    """Large"""

    LEFT = "#LEFT"
    """Left"""

    RIGHT = "#RIGHT"
    """Right"""

    UP = "#UP"
    """Up"""

    DOWN = "#DOWN"
    """Down"""

    FRONT = "#FRONT"
    """Front"""

    BACK = "#BACK"
    """Back"""

    CENTER = "#CENTER"
    """Center"""

    TOP = "#TOP"
    """Top"""

    BOTTOM = "#BOTTOM"
    """Bottom"""

    TOP_LEFT = "#TOP_LEFT"
    """Top Left"""

    TOP_CENTER = "#TOP_CENTER"
    """Top Center"""

    TOP_RIGHT = "#TOP_RIGHT"
    """Top Right"""

    CENTER_LEFT = "#CENTER_LEFT"
    """Center Left"""

    CENTER_RIGHT = "#CENTER_RIGHT"
    """Center Right"""

    BOTTOM_LEFT = "#BOTTOM_LEFT"
    """Bottom Left"""

    BOTTOM_CENTER = "#BOTTOM_CENTER"
    """Bottom Center"""

    BOTTOM_RIGHT = "#BOTTOM_RIGHT"
    """Bottom Right"""

    CLOCKWISE = "#CLOCKWISE"
    """Clockwise"""

    COUNTERCLOCKWISE = "#COUNTERCLOCKWISE"
    """Counterclockwise"""

    FORWARD = "#FORWARD"
    """Forward"""

    BACKWARD = "#BACKWARD"
    """Backward"""

    DEFAULT = "#DEFAULT"
    """Default"""

    NEUTRAL = "#NEUTRAL"
    """Neutral"""

    RED = "#RED"
    """Red"""

    GREEN = "#GREEN"
    """Green"""

    BLUE = "#BLUE"
    """Blue"""

    YELLOW = "#YELLOW"
    """Yellow"""

    PURPLE = "#PURPLE"
    """Purple"""

    CYAN = "#CYAN"
    """Cyan"""

    SIMPLE = "#SIMPLE"
    """Simple"""

    EASY = "#EASY"
    """Easy"""

    NORMAL = "#NORMAL"
    """Normal"""

    HARD = "#HARD"
    """Hard"""

    EXPERT = "#EXPERT"
    """Expert"""

    MASTER = "#MASTER"
    """Master"""

    PRO = "#PRO"
    """Pro"""

    TECHNICAL = "#TECHNICAL"
    """Technical"""

    SPECIAL = "#SPECIAL"
    """Special"""

    APPEND = "#APPEND"
    """Append"""

    POST_PLACEHOLDER = "#POST_PLACEHOLDER"
    """Enter post..."""

    PLAYLIST_PLACEHOLDER = "#PLAYLIST_PLACEHOLDER"
    """Enter playlist..."""

    LEVEL_PLACEHOLDER = "#LEVEL_PLACEHOLDER"
    """Enter level..."""

    SKIN_PLACEHOLDER = "#SKIN_PLACEHOLDER"
    """Enter skin..."""

    BACKGROUND_PLACEHOLDER = "#BACKGROUND_PLACEHOLDER"
    """Enter background..."""

    EFFECT_PLACEHOLDER = "#EFFECT_PLACEHOLDER"
    """Enter sFX..."""

    PARTICLE_PLACEHOLDER = "#PARTICLE_PLACEHOLDER"
    """Enter particle..."""

    ENGINE_PLACEHOLDER = "#ENGINE_PLACEHOLDER"
    """Enter engine..."""

    REPLAY_PLACEHOLDER = "#REPLAY_PLACEHOLDER"
    """Enter replay..."""

    USER_PLACEHOLDER = "#USER_PLACEHOLDER"
    """Enter user..."""

    ROOM_PLACEHOLDER = "#ROOM_PLACEHOLDER"
    """Enter room..."""

    KEYWORDS_PLACEHOLDER = "#KEYWORDS_PLACEHOLDER"
    """Enter keywords..."""

    NAME_PLACEHOLDER = "#NAME_PLACEHOLDER"
    """Enter name..."""

    RATING_PLACEHOLDER = "#RATING_PLACEHOLDER"
    """Enter rating..."""

    RATING_MINIMUM_PLACEHOLDER = "#RATING_MINIMUM_PLACEHOLDER"
    """Enter minimum rating..."""

    RATING_MAXIMUM_PLACEHOLDER = "#RATING_MAXIMUM_PLACEHOLDER"
    """Enter maximum rating..."""

    TITLE_PLACEHOLDER = "#TITLE_PLACEHOLDER"
    """Enter title..."""

    SUBTITLE_PLACEHOLDER = "#SUBTITLE_PLACEHOLDER"
    """Enter subtitle..."""

    ARTISTS_PLACEHOLDER = "#ARTISTS_PLACEHOLDER"
    """Enter artists..."""

    TIME_PLACEHOLDER = "#TIME_PLACEHOLDER"
    """Enter time..."""

    AUTHOR_PLACEHOLDER = "#AUTHOR_PLACEHOLDER"
    """Enter author..."""

    DESCRIPTION_PLACEHOLDER = "#DESCRIPTION_PLACEHOLDER"
    """Enter description..."""

    GENRE_PLACEHOLDER = "#GENRE_PLACEHOLDER"
    """Enter genre..."""

    TYPE_PLACEHOLDER = "#TYPE_PLACEHOLDER"
    """Enter type..."""

    CATEGORY_PLACEHOLDER = "#CATEGORY_PLACEHOLDER"
    """Enter category..."""

    LANGUAGE_PLACEHOLDER = "#LANGUAGE_PLACEHOLDER"
    """Enter language..."""

    DIFFICULTY_PLACEHOLDER = "#DIFFICULTY_PLACEHOLDER"
    """Enter difficulty..."""

    LENGTH_PLACEHOLDER = "#LENGTH_PLACEHOLDER"
    """Enter length..."""

    LENGTH_MINIMUM_PLACEHOLDER = "#LENGTH_MINIMUM_PLACEHOLDER"
    """Enter minimum length..."""

    LENGTH_MAXIMUM_PLACEHOLDER = "#LENGTH_MAXIMUM_PLACEHOLDER"
    """Enter maximum length..."""

    ADDITIONAL_INFORMATION_PLACEHOLDER = "#ADDITIONAL_INFORMATION_PLACEHOLDER"
    """Enter additional information..."""

    TIMEZONE_PLACEHOLDER = "#TIMEZONE_PLACEHOLDER"
    """Enter timezone..."""

    REGION_PLACEHOLDER = "#REGION_PLACEHOLDER"
    """Enter region..."""

    CONTENT_PLACEHOLDER = "#CONTENT_PLACEHOLDER"
    """Enter content..."""

    COMMENT_PLACEHOLDER = "#COMMENT_PLACEHOLDER"
    """Enter comment..."""

    REVIEW_PLACEHOLDER = "#REVIEW_PLACEHOLDER"
    """Enter review..."""

    REPLY_PLACEHOLDER = "#REPLY_PLACEHOLDER"
    """Enter reply..."""

    MESSAGE_PLACEHOLDER = "#MESSAGE_PLACEHOLDER"
    """Enter message..."""

    ROLE_PLACEHOLDER = "#ROLE_PLACEHOLDER"
    """Enter role..."""

    PERMISSION_PLACEHOLDER = "#PERMISSION_PLACEHOLDER"
    """Enter permission..."""

    PERCENTAGE_UNIT = "#PERCENTAGE_UNIT"
    """{0}%"""

    YEAR_UNIT = "#YEAR_UNIT"
    """{0} yr"""

    MONTH_UNIT = "#MONTH_UNIT"
    """{0} mo"""

    DAY_UNIT = "#DAY_UNIT"
    """{0} d"""

    HOUR_UNIT = "#HOUR_UNIT"
    """{0} h"""

    MINUTE_UNIT = "#MINUTE_UNIT"
    """{0} m"""

    SECOND_UNIT = "#SECOND_UNIT"
    """{0} s"""

    MILLISECOND_UNIT = "#MILLISECOND_UNIT"
    """{0} ms"""

    YEAR_PAST = "#YEAR_PAST"
    """{0} yr ago"""

    MONTH_PAST = "#MONTH_PAST"
    """{0} mo ago"""

    DAY_PAST = "#DAY_PAST"
    """{0} d ago"""

    HOUR_PAST = "#HOUR_PAST"
    """{0} h ago"""

    MINUTE_PAST = "#MINUTE_PAST"
    """{0} m ago"""

    SECOND_PAST = "#SECOND_PAST"
    """{0} s ago"""

    MILLISECOND_PAST = "#MILLISECOND_PAST"
    """{0} ms ago"""

    YEAR_FUTURE = "#YEAR_FUTURE"
    """In {0} yr"""

    MONTH_FUTURE = "#MONTH_FUTURE"
    """In {0} mo"""

    DAY_FUTURE = "#DAY_FUTURE"
    """In {0} d"""

    HOUR_FUTURE = "#HOUR_FUTURE"
    """In {0} h"""

    MINUTE_FUTURE = "#MINUTE_FUTURE"
    """In {0} m"""

    SECOND_FUTURE = "#SECOND_FUTURE"
    """In {0} s"""

    MILLISECOND_FUTURE = "#MILLISECOND_FUTURE"
    """In {0} ms"""

    TAP = "#TAP"
    """Tap"""

    TAP_HOLD = "#TAP_HOLD"
    """Tap and Hold"""

    TAP_RELEASE = "#TAP_RELEASE"
    """Tap and Release"""

    TAP_FLICK = "#TAP_FLICK"
    """Tap and Flick"""

    TAP_SLIDE = "#TAP_SLIDE"
    """Tap and Slide"""

    HOLD = "#HOLD"
    """Hold"""

    HOLD_SLIDE = "#HOLD_SLIDE"
    """Hold and Slide"""

    HOLD_FOLLOW = "#HOLD_FOLLOW"
    """Hold and Follow"""

    RELEASE = "#RELEASE"
    """Release"""

    FLICK = "#FLICK"
    """Flick"""

    SLIDE = "#SLIDE"
    """Slide"""

    SLIDE_FLICK = "#SLIDE_FLICK"
    """Slide and Flick"""

    AVOID = "#AVOID"
    """Avoid"""

    JIGGLE = "#JIGGLE"
    """Jiggle"""

    NEWEST = "#NEWEST"
    """Newest"""

    OLDEST = "#OLDEST"
    """Oldest"""

    RECOMMENDED = "#RECOMMENDED"
    """Recommended"""

    POPULAR = "#POPULAR"
    """Popular"""

    FEATURED = "#FEATURED"
    """Featured"""

    COMPETITIVE = "#COMPETITIVE"
    """Competitive"""

    TOURNAMENT = "#TOURNAMENT"
    """Tournament"""

    HOLIDAY = "#HOLIDAY"
    """Holiday"""

    LIMITED = "#LIMITED"
    """Limited"""

    ANNOUNCEMENT = "#ANNOUNCEMENT"
    """Announcement"""

    INFORMATION = "#INFORMATION"
    """Information"""

    HELP = "#HELP"
    """Help"""

    MAINTENANCE = "#MAINTENANCE"
    """Maintenance"""

    EVENT = "#EVENT"
    """Event"""

    UPDATE = "#UPDATE"
    """Update"""

    SEARCH = "#SEARCH"
    """Search"""

    ADVANCED = "#ADVANCED"
    """Advanced"""

    RELATED = "#RELATED"
    """Related"""

    SAME_AUTHOR = "#SAME_AUTHOR"
    """Same Author"""

    SAME_ARTISTS = "#SAME_ARTISTS"
    """Same Artists"""

    SAME_RATING = "#SAME_RATING"
    """Same Rating"""

    SAME_CATEGORY = "#SAME_CATEGORY"
    """Same Category"""

    SAME_DIFFICULTY = "#SAME_DIFFICULTY"
    """Same Difficulty"""

    SAME_GENRE = "#SAME_GENRE"
    """Same Genre"""

    SAME_VERSION = "#SAME_VERSION"
    """Same Version"""

    OTHER_AUTHORS = "#OTHER_AUTHORS"
    """Other Authors"""

    OTHER_ARTISTS = "#OTHER_ARTISTS"
    """Other Artists"""

    OTHER_RATINGS = "#OTHER_RATINGS"
    """Other Ratings"""

    OTHER_CATEGORIES = "#OTHER_CATEGORIES"
    """Other Categories"""

    OTHER_DIFFICULTIES = "#OTHER_DIFFICULTIES"
    """Other Difficulties"""

    OTHER_GENRES = "#OTHER_GENRES"
    """Other Genres"""

    OTHER_VERSIONS = "#OTHER_VERSIONS"
    """Other Versions"""

    DRAFT = "#DRAFT"
    """Draft"""

    PUBLIC = "#PUBLIC"
    """Public"""

    PRIVATE = "#PRIVATE"
    """Private"""

    POP = "#POP"
    """Pop"""

    ROCK = "#ROCK"
    """Rock"""

    HIPHOP = "#HIPHOP"
    """Hip Hop"""

    COUNTRY = "#COUNTRY"
    """Country"""

    ELECTRONIC = "#ELECTRONIC"
    """Electronic"""

    METAL = "#METAL"
    """Metal"""

    CLASSICAL = "#CLASSICAL"
    """Classical"""

    FOLK = "#FOLK"
    """Folk"""

    INDIE = "#INDIE"
    """Indie"""

    ANIME = "#ANIME"
    """Anime"""

    VOCALOID = "#VOCALOID"
    """Vocaloid"""

    REMIX = "#REMIX"
    """Remix"""

    INSTRUMENTAL = "#INSTRUMENTAL"
    """Instrumental"""

    SHORT_VERSION = "#SHORT_VERSION"
    """Short Version"""

    LONG_VERSION = "#LONG_VERSION"
    """Long Version"""

    CUT_VERSION = "#CUT_VERSION"
    """Cut Version"""

    FULL_VERSION = "#FULL_VERSION"
    """Full Version"""

    EXTENDED_VERSION = "#EXTENDED_VERSION"
    """Extended Version"""

    LIVE_VERSION = "#LIVE_VERSION"
    """Live Version"""

    EXPLICIT = "#EXPLICIT"
    """Explicit"""

    MULTI_FINGER = "#MULTI_FINGER"
    """Multi Finger"""

    FULL_HAND = "#FULL_HAND"
    """Full Hand"""

    CROSS_HAND = "#CROSS_HAND"
    """Cross Hand"""

    GIMMICK = "#GIMMICK"
    """Gimmick"""

    COLLABORATION = "#COLLABORATION"
    """Collaboration"""

    COLLABORATOR = "#COLLABORATOR"
    """Collaborator"""

    REPORT = "#REPORT"
    """Report"""

    REASON = "#REASON"
    """Reason"""

    ILLEGAL_ACTIVITIES = "#ILLEGAL_ACTIVITIES"
    """Illegal Activities"""

    CHEATING = "#CHEATING"
    """Cheating"""

    AFK = "#AFK"
    """AFK"""

    SPAMMING = "#SPAMMING"
    """Spamming"""

    VERBAL_ABUSE = "#VERBAL_ABUSE"
    """Verbal Abuse"""

    INAPPROPRIATE_LANGUAGE = "#INAPPROPRIATE_LANGUAGE"
    """Inappropriate Language"""

    NEGATIVE_ATTITUDE = "#NEGATIVE_ATTITUDE"
    """Negative Attitude"""

    DNF = "#DNF"
    """DNF"""

    SUGGESTIONS = "#SUGGESTIONS"
    """Suggestions"""

    SUGGESTIONS_PER_PLAYER = "#SUGGESTIONS_PER_PLAYER"
    """Suggestions per Player"""

    MATCH_SCORING = "#MATCH_SCORING"
    """Match Scoring"""

    MATCH_TIEBREAKER = "#MATCH_TIEBREAKER"
    """Match Tiebreaker"""

    MATCH_COUNT = "#MATCH_COUNT"
    """Match Count"""

    MATCH_LIMIT = "#MATCH_LIMIT"
    """Match Limit"""

    ROUND_SCORING = "#ROUND_SCORING"
    """Round Scoring"""

    ROUND_TIEBREAKER = "#ROUND_TIEBREAKER"
    """Round Tiebreaker"""

    ROUND_COUNT = "#ROUND_COUNT"
    """Round Count"""

    ROUND_LIMIT = "#ROUND_LIMIT"
    """Round Limit"""

    TEAM_SCORING = "#TEAM_SCORING"
    """Team Scoring"""

    TEAM_TIEBREAKER = "#TEAM_TIEBREAKER"
    """Team Tiebreaker"""

    TEAM_COUNT = "#TEAM_COUNT"
    """Team Count"""

    TEAM_LIMIT = "#TEAM_LIMIT"
    """Team Limit"""

    QUALIFIED = "#QUALIFIED"
    """Qualified"""

    DISQUALIFIED = "#DISQUALIFIED"
    """Disqualified"""

    RANKING = "#RANKING"
    """Ranking"""

    SCORE = "#SCORE"
    """Score"""

    OWNER = "#OWNER"
    """Owner"""

    ADMIN = "#ADMIN"
    """Admin"""

    MODERATOR = "#MODERATOR"
    """Moderator"""

    REVIEWER = "#REVIEWER"
    """Reviewer"""

    BANNED = "#BANNED"
    """Banned"""

    PLAYER = "#PLAYER"
    """Player"""

    SPECTATOR = "#SPECTATOR"
    """Spectator"""

    REFEREE = "#REFEREE"
    """Referee"""

    ELIMINATED = "#ELIMINATED"
    """Eliminated"""

    FINALIST = "#FINALIST"
    """Finalist"""

    FINISHED = "#FINISHED"
    """Finished"""

    WINNER = "#WINNER"
    """Winner"""

    GOLD_MEDAL = "#GOLD_MEDAL"
    """Gold Medal"""

    SILVER_MEDAL = "#SILVER_MEDAL"
    """Silver Medal"""

    BRONZE_MEDAL = "#BRONZE_MEDAL"
    """Bronze Medal"""

    TEAM1 = "#TEAM_1"
    """Team 1"""

    TEAM2 = "#TEAM_2"
    """Team 2"""

    TEAM3 = "#TEAM_3"
    """Team 3"""

    TEAM4 = "#TEAM_4"
    """Team 4"""

    TEAM5 = "#TEAM_5"
    """Team 5"""

    TEAM6 = "#TEAM_6"
    """Team 6"""

    TEAM7 = "#TEAM_7"
    """Team 7"""

    TEAM8 = "#TEAM_8"
    """Team 8"""

    TEAM_RED = "#TEAM_RED"
    """Team Red"""

    TEAM_GREEN = "#TEAM_GREEN"
    """Team Green"""

    TEAM_BLUE = "#TEAM_BLUE"
    """Team Blue"""

    TEAM_YELLOW = "#TEAM_YELLOW"
    """Team Yellow"""

    TEAM_PURPLE = "#TEAM_PURPLE"
    """Team Purple"""

    TEAM_CYAN = "#TEAM_CYAN"
    """Team Cyan"""

    TEAM_WHITE = "#TEAM_WHITE"
    """Team White"""

    TEAM_BLACK = "#TEAM_BLACK"
    """Team Black"""

    ADD = "#ADD"
    """Add"""

    ADDED = "#ADDED"
    """Added"""

    CREATE = "#CREATE"
    """Create"""

    CREATED = "#CREATED"
    """Created"""

    REPLY = "#REPLY"
    """Reply"""

    REPLIED = "#REPLIED"
    """Replied"""

    REVIEW = "#REVIEW"
    """Review"""

    REVIEWING = "#REVIEWING"
    """Reviewing"""

    REVIEWED = "#REVIEWED"
    """Reviewed"""

    VERIFY = "#VERIFY"
    """Verify"""

    VERIFYING = "#VERIFYING"
    """Verifying"""

    VERIFIED = "#VERIFIED"
    """Verified"""

    UPLOAD = "#UPLOAD"
    """Upload"""

    UPLOADING = "#UPLOADING"
    """Uploading"""

    UPLOADED = "#UPLOADED"
    """Uploaded"""

    SUBMIT = "#SUBMIT"
    """Submit"""

    SUBMITTING = "#SUBMITTING"
    """Submitting"""

    SUBMITTED = "#SUBMITTED"
    """Submitted"""

    EDIT = "#EDIT"
    """Edit"""

    EDITING = "#EDITING"
    """Editing"""

    EDITED = "#EDITED"
    """Edited"""

    LIKE = "#LIKE"
    """Like"""

    LIKED = "#LIKED"
    """Liked"""

    DISLIKE = "#DISLIKE"
    """Dislike"""

    DISLIKED = "#DISLIKED"
    """Disliked"""

    BOOKMARK = "#BOOKMARK"
    """Bookmark"""

    BOOKMARKED = "#BOOKMARKED"
    """Bookmarked"""

    DELETE = "#DELETE"
    """Delete"""

    DELETING = "#DELETING"
    """Deleting"""

    DELETED = "#DELETED"
    """Deleted"""

    REMOVE = "#REMOVE"
    """Remove"""

    REMOVING = "#REMOVING"
    """Removing"""

    REMOVED = "#REMOVED"
    """Removed"""

    RESTORE = "#RESTORE"
    """Restore"""

    RESTORING = "#RESTORING"
    """Restoring"""

    RESTORED = "#RESTORED"
    """Restored"""

    CONFIRM = "#CONFIRM"
    """Confirm"""

    CONFIRMED = "#CONFIRMED"
    """Confirmed"""

    CANCEL = "#CANCEL"
    """Cancel"""

    CANCELED = "#CANCELED"
    """Canceled"""

    INCREASE = "#INCREASE"
    """Increase"""

    DECREASE = "#DECREASE"
    """Decrease"""

    UPVOTE = "#UPVOTE"
    """Upvote"""

    UPVOTED = "#UPVOTED"
    """Upvoted"""

    DOWNVOTE = "#DOWNVOTE"
    """Downvote"""

    DOWNVOTED = "#DOWNVOTED"
    """Downvoted"""

    AGREE = "#AGREE"
    """Agree"""

    AGREED = "#AGREED"
    """Agreed"""

    DISAGREE = "#DISAGREE"
    """Disagree"""

    DISAGREED = "#DISAGREED"
    """Disagreed"""

    LOCK = "#LOCK"
    """Lock"""

    LOCKED = "#LOCKED"
    """Locked"""

    UNLOCK = "#UNLOCK"
    """Unlock"""

    UNLOCKED = "#UNLOCKED"
    """Unlocked"""

    PIN = "#PIN"
    """Pin"""

    PINNED = "#PINNED"
    """Pinned"""

    UNPIN = "#UNPIN"
    """Unpin"""

    UNPINNED = "#UNPINNED"
    """Unpinned"""

    FOLLOW = "#FOLLOW"
    """Follow"""

    FOLLOWING = "#FOLLOWING"
    """Following"""

    FOLLOWED = "#FOLLOWED"
    """Followed"""

    UNFOLLOW = "#UNFOLLOW"
    """Unfollow"""

    SUBSCRIBE = "#SUBSCRIBE"
    """Subscribe"""

    SUBSCRIBING = "#SUBSCRIBING"
    """Subscribing"""

    SUBSCRIBED = "#SUBSCRIBED"
    """Subscribed"""

    UNSUBSCRIBE = "#UNSUBSCRIBE"
    """Unsubscribe"""

    PUBLISH = "#PUBLISH"
    """Publish"""

    PUBLISHING = "#PUBLISHING"
    """Publishing"""

    PUBLISHED = "#PUBLISHED"
    """Published"""

    UNPUBLISH = "#UNPUBLISH"
    """Unpublish"""

    SHOW = "#SHOW"
    """Show"""

    HIDE = "#HIDE"
    """Hide"""

    ALLOW = "#ALLOW"
    """Allow"""

    ALLOWED = "#ALLOWED"
    """Allowed"""

    DISALLOW = "#DISALLOW"
    """Disallow"""

    DISALLOWED = "#DISALLOWED"
    """Disallowed"""

    APPROVE = "#APPROVE"
    """Approve"""

    APPROVED = "#APPROVED"
    """Approved"""

    DENY = "#DENY"
    """Deny"""

    DENIED = "#DENIED"
    """Denied"""

    ACCEPT = "#ACCEPT"
    """Accept"""

    ACCEPTED = "#ACCEPTED"
    """Accepted"""

    REJECT = "#REJECT"
    """Reject"""

    REJECTED = "#REJECTED"
    """Rejected"""

    STAR = "#STAR"
    """Star"""

    STARRED = "#STARRED"
    """Starred"""
