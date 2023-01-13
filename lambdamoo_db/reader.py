from io import TextIOWrapper
import re
from typing import Any, NoReturn
from lambdamoo_db.database import (
    Activation,
    MooDatabase,
    MooObject,
    Property,
    QueuedTask,
    Verb,
)
from lambdamoo_db.enums import DBVersions, MooTypes


def load(filename: str) -> MooDatabase:
    with open(filename, "r") as f:
        r = Reader(f)
        return r.parse()


versionRe = re.compile(r"\*\* LambdaMOO Database, Format Version (?P<version>\d+) \*\*")
varCountRe = re.compile(r"(?P<count>\d+) variables")
clockCountRe = re.compile(r"(?P<count>\d+) clocks")
taskCountRe = re.compile(r"(?P<count>\d+) queued tasks")
taskHeaderRe = re.compile(r"\d+ (\d+) (\d+) (\d+)")
activationHeaderRe = re.compile(
    r"-?(\d+) -?\d+ -?\d+ -?(\d+) -?\d+ -?(\d+) -?(\d+) -?\d+ -?(\d+)"
)
pendingValueRe = re.compile(r"(?P<count>\d+) values pending finalization")
suspendedTaskCountRe = re.compile(r"(?P<count>\d+) suspended tasks")
suspendedTaskHeaderRe = re.compile(
    r"(?P<startTime>\d+) (?P<id>\d+)(?P<endchar>\n| )(?P<value>|.+\n)"
)
interruptedTaskCountRe = re.compile(r"(?P<count>\d+) interrupted tasks")
interruptedTaskHeaderRe = re.compile(r"(?P<id>) interrupted reading task")
vmHeaderRe = re.compile(
    r"(?P<top>\d+) (?P<vector>-?\d+) (?P<funcId>\d+)(\n| (?P<maxStackframes>\d))"
)
connectionCountRe = re.compile(
    r"(?P<count>\d+) active connections(?P<listener_tag>| with listeners)"
)


class Reader:
    def __init__(self, fio: TextIOWrapper) -> None:
        self.file = fio
        self.line = 0

    def parse_error(self, message: str) -> NoReturn:
        raise Exception(f"{message} @ line {self.line}")

    def parse(self) -> "MooDatabase":
        db = MooDatabase()
        db.versionstring = self.readString()
        db.version = int(versionRe.match(db.versionstring).group("version"))
        match db.version:
            case 4:
                self.parse_v4(db)
            case 17:
                self.parse_v17(db)
            case _:
                self.parse_error(f"Unknown db version {db.version}")
        return db

    def parse_v4(self, db: MooDatabase) -> None:
        db.total_objects = self.readInt()
        db.total_verbs = self.readInt()
        self.readString()  # dummy
        self.readPlayers(db)
        self.readObjects(db)
        self.readVerbs(db)
        self.readClocks()
        self.readTaskQueue(db)

    def parse_v17(self, db: MooDatabase) -> None:
        self.readPlayers(db)
        self.readPending()
        self.readClocks()
        self.readTaskQueue(db)
        self.readSuspendedTasks(db)
        self.readInterruptedTasks(db)
        self.readConnections()
        db.total_objects = self.readInt()
        self.readObjects(db)
        db.total_verbs = self.readInt()
        self.readVerbs(db)

    def readValue(self) -> Any:
        val_type = self.readInt()
        match val_type:
            case MooTypes.STR:
                return self.readString()
            case MooTypes.OBJ:
                return self.readObjnum()
            case MooTypes.ANON:
                return self.readAnon()
            case MooTypes.INT:
                return self.readInt()
            case MooTypes.FLOAT:
                return self.readFloat()
            case MooTypes.ERR:
                return self.readErr()
            case MooTypes.LIST:
                return self.readList()
            case MooTypes.CLEAR:
                pass
            case MooTypes.NONE:
                pass
            case MooTypes.MAP:
                return self.readMap()
            case MooTypes.BOOL:
                return self.readBool()
            case _:
                self.parse_error(f"unknown type {val_type}")

    def readString(self) -> str:
        """Read a string from the database file"""
        self.line += 1
        return self.file.readline().rstrip("\r\n")

    def readInt(self) -> int:
        """Read an integer from the database file"""
        return int(self.readString())

    def readErr(self) -> int:
        return self.readInt()

    def readFloat(self) -> float:
        return float(self.readString())

    def readObjnum(self) -> int:
        return self.readInt()

    def readBool(self) -> int:
        return bool(self.readString())

    def readList(self) -> list[Any]:
        length = self.readInt()
        result = []
        for _ in range(length):
            result.append(self.readValue())
        return result

    def readMap(self) -> dict:
        # self.parse_error(f'MAP @ Line {self.line}')
        items = self.readInt()
        map = {}
        for _ in range(items):
            key = self.readValue()
            val = self.readValue()
            map[key] = val
        return map

    def readObject_v4(self, db: MooDatabase) -> MooObject | None:
        objNumber = self.readString()
        if not objNumber.startswith("#"):
            self.parse_error("object number does not have #")

        if "recycled" in objNumber:
            return None

        oid = int(objNumber[1:])
        name = self.readString()
        self.readString()  # blankline
        flags = self.readInt()
        owner = self.readObjnum()
        location = self.readObjnum()
        firstContent = self.readInt()
        neighbor = self.readInt()
        parent = self.readObjnum()
        firstChild = self.readInt()
        sibling = self.readInt()
        obj = MooObject(oid, name, flags, owner, location, parent)
        numVerbs = self.readInt()
        for _ in range(numVerbs):
            self.readVerbMetadata(obj)

        self.readProperties(obj)
        return obj

    def readObject_ng(self, db: MooDatabase) -> MooObject | None:
        objNumber = self.readString()
        if not objNumber.startswith("#"):
            self.parse_error("object number does not have #")

        if "recycled" in objNumber:
            return None

        oid = int(objNumber[1:])
        name = self.readString()
        flags = self.readInt()
        owner = self.readObjnum()
        location = self.readValue()
        if db.version >= DBVersions.DBV_Last_Move:
            last_move = self.readValue()

        contents = self.readValue()
        parents = self.readValue()
        children = self.readValue()
        obj = MooObject(oid, name, flags, owner, location, parents)
        numVerbs = self.readInt()
        for _ in range(numVerbs):
            self.readVerbMetadata(obj)

        self.readProperties(obj)
        return obj

    def readConnections(self) -> None:
        header = self.readString()
        headerMatch = connectionCountRe.match(header)
        if not headerMatch:
            self.parse_error("Bad active connections header line")

        count = int(headerMatch.group("count"))
        for _ in range(count):
            # Read and discard `count` lines; this data is useless to us.
            self.readString()

    def readVerbs(self, db: MooDatabase) -> None:
        for _ in range(db.total_verbs):
            self.readVerb(db)

    def readVerb(self, db: MooDatabase) -> None:
        verbLocation = self.readString()
        if ":" not in verbLocation:
            self.parse_error("verb does not have seperator")

        sep = verbLocation.index(":")
        objNumber = int(verbLocation[1:sep])
        verbNumber = int(verbLocation[sep + 1 :])
        code = self.readCode()
        obj = db.objects.get(objNumber)
        if not obj:
            self.parse_error(f"object {objNumber} not found")

        verb = obj.verbs[verbNumber]
        if not verb:
            self.parse_error(f"verb ${verbNumber} not found on object ${objNumber}")

        verb.code = code

    def readCode(self) -> list[str]:
        code = []
        lastLine = self.readString()
        while lastLine != ".":
            code.append(lastLine)
            lastLine = self.readString()
        return code

    def readPlayers(self, db: MooDatabase) -> None:
        db.total_players = self.readInt()
        db.players = []
        for _ in range(db.total_players):
            db.players.append(self.readObjnum())
        assert db.total_players == len(db.players)

    def readObjects(self, db: MooDatabase) -> None:
        db.objects = {}
        for _ in range(db.total_objects):
            if db.version == 4:
                obj = self.readObject_v4(db)
            else:
                obj = self.readObject_ng(db)
            if not obj:
                continue
            db.objects[obj.id] = obj

    def readVerbMetadata(self, obj: MooObject) -> None:
        name = self.readString()
        owner = self.readObjnum()
        perms = self.readInt()
        preps = self.readInt()
        verb = Verb(name, owner, perms, preps)
        obj.verbs.append(verb)

    def readProperties(self, obj: MooObject):
        numProperties = self.readInt()
        propertyNames = []
        for _ in range(numProperties):
            propertyNames.append(self.readString())
        numPropdefs = self.readInt()
        for _ in range(numPropdefs):
            propertyName = None
            if propertyNames:
                propertyName = propertyNames.pop(0)
            value = self.readValue()
            owner = self.readObjnum()
            perms = self.readInt()
            property = Property(propertyName, value, owner, perms)
            obj.properties.append(property)

    def readPending(self) -> None:
        valueLine = self.readString()
        valueMatch = pendingValueRe.match(valueLine)
        if not valueMatch:
            self.parse_error("Bad pending finalizations")

        finalizationCount = int(valueMatch.group("count"))
        for _ in range(finalizationCount):
            self.readValue()

    def readClocks(self) -> None:
        clockLine = self.readString()
        clockMatch = clockCountRe.match(clockLine)
        if not clockMatch:
            self.parse_error("Could not find clock definitions")
        numClocks = int(clockMatch.group("count"))
        for _ in range(numClocks):
            self.readClock()

    def readClock(self) -> None:
        """Obsolete"""
        self.readString()

    def readTaskQueue(self, db: MooDatabase) -> None:
        queuedTasksLine = self.readString()
        queuedTasksMatch = taskCountRe.match(queuedTasksLine)
        if not queuedTasksMatch:
            self.parse_error("Could not find task queue")

        numTasks = int(queuedTasksMatch.group("count"))
        db.queuedTasks = []
        for _ in range(numTasks):
            self.readQueuedTask(db)

    def readQueuedTask(self, db: MooDatabase) -> None:
        headerLine = self.readString()
        headerMatch = taskHeaderRe.match(headerLine)
        if not headerMatch:
            self.parse_error("Could not find task header")

        firstLineno = int(headerMatch[1])
        st = int(headerMatch[2])
        id = int(headerMatch[3])
        task = QueuedTask(firstLineno, id, st)
        activation = self.readActivation(db)
        task.activation = activation
        task.rtEnv = self.readRTEnv()
        task.code = self.readCode()
        db.queuedTasks.append(task)

    def readActivation(self, db: MooDatabase) -> Activation:
        _ = self.readValue()
        if db.version >= DBVersions.DBV_This:
            _this = self.readValue()
        if db.version >= DBVersions.DBV_Anon:
            _vloc = self.readValue()
        if db.version >= DBVersions.DBV_Threaded:
            _threaded = self.readInt()
        # else
        #     _threaded = DEFAULT_THREAD_MODE;

        headerLine = self.readString()
        headerMatch = activationHeaderRe.match(headerLine)
        if not headerMatch:  # or headerMatch.length !== 6) {
            self.parse_error("Could not find activation header")

        activation = Activation()
        activation.this = int(headerMatch[1])
        activation.player = int(headerMatch[2])
        activation.programmer = int(headerMatch[3])
        activation.vloc = int(headerMatch[4])
        activation.debug = bool(headerMatch[5])
        self.readString()  # /* Was argstr*/
        self.readString()  # /* Was dobjstr*/
        self.readString()  # /* Was prepstr*/
        self.readString()  # /* Was iobjstr*/
        activation.verb = self.readString()
        activation.verbname = self.readString()
        return activation

    def readRTEnv(self) -> dict[str, Any]:
        varCountLine = self.readString()
        varCountMatch = varCountRe.match(varCountLine)
        if not varCountMatch:
            self.parse_error("Could not find variable count for RT Env")

        varCount = int(varCountMatch.group("count"))
        rtEnv = {}
        for _ in range(varCount):
            name = self.readString()
            value = self.readValue()
            rtEnv[name] = value
        return rtEnv

    def readSuspendedTasks(self, db: MooDatabase) -> None:
        valueLine = self.readString()
        suspendedMatch = suspendedTaskCountRe.match(valueLine)
        if not suspendedMatch:
            self.parse_error("Bad suspended tasks header")

        count = int(suspendedMatch.group("count"))
        for _ in range(count):
            self.readSuspendedTask(db)

    def readSuspendedTask(self, db: MooDatabase) -> None:
        headerLine = self.readString()
        taskMatch = suspendedTaskHeaderRe.match(headerLine)
        if not taskMatch:
            self.parse_error(f"Bad suspended task header: {headerLine}")

        id = int(taskMatch.group("id"))
        startTime = int(taskMatch.group("startTime"))
        task = QueuedTask(
            0, id, startTime
        )  # Set line number to 0 for a suspended task since we don't know it (only opcodes, not text)
        if taskMatch.group("value"):
            task.value = self.readValue()

        db.queuedTasks.append(task)

    def readInterruptedTasks(self, db: MooDatabase):
        valueLine = self.readString()
        interruptedMatch = interruptedTaskCountRe.match(valueLine)
        if not interruptedMatch:
            self.parse_error("Bad suspended tasks header")

        count = int(interruptedMatch.group("count"))
        for _ in range(count):
            self.readInterruptedTask(db)

    def readInterruptedTask(self, db: MooDatabase) -> None:
        header = self.readString()
        headerMatch = interruptedTaskHeaderRe.match(header)
        if not headerMatch:
            self.parse_error("Bad interrupted tasks header")
        raise NotImplemented()
        """
        int task_id;
        const char *status;
        vm the_vm;

        # if (dbio_scanf("%d ", &task_id) != 1) {
        #     errlog("READ_TASK_QUEUE: Bad interrupted task header, count = %d\n",
        #            interrupted_count);
        #     return 0;
        # }
        if ((status = dbio_read_string()) == nullptr) {
            errlog("READ_TASK_QUEUE: Bad interrupted task status, count = %d\n",
                   interrupted_count);
            return 0;
        }

        if (!(the_vm = read_vm(task_id))) {
            errlog("READ_TASK_QUEUE: Bad interrupted task vm, count = %d\n",
                   interrupted_count);
            return 0;
        }

        task *t = (task *)mymalloc(sizeof(task), M_TASK);
        t->kind = TASK_SUSPENDED;
        t->t.suspended.start_tv.tv_sec = 0;
        t->t.suspended.start_tv.tv_usec = 0;
        t->t.suspended.value.type = TYPE_ERR;
        t->t.suspended.value.v.err = E_INTRPT;
        t->t.suspended.the_vm = the_vm;
        enqueue_waiting(t);
        """
