import sys
import os
import json

srcText = ""
AST = ""
srcPtr = 0
bracketsExm = 0
memAry = []
regAry = []
wireAry = []
tempAry = []
jsonMap = None
# cases 条件栈：进入 (cases ...) 分支时压入 [指示寄存器名, 位下标, 要求值]
# 要求值 1 表示该位为 1（分支条件写作 0），0 表示该位为 0（分支条件写作 ~0）
caseStack = []
# 端口收集表：信号名 -> 位下标集合
portRead = {}
portMapOut = {}
portMovOut = {}
# temp 变量的声明初值快照，每次展开前用它复位
tempInit = []
# 当前 model 内 0/1 常量信号是否已声明过
constDeclared = {}
# 存储体实现方式：expand 展开成触发器阵列（可仿真），subckt 发黑盒交给 BSRAM（上板用）
memMode = "expand"
# mux 生成的内部信号序号：同一个设计里每个 mux 的内部名必须唯一
muxSeq = 0
# 存储体接口绑定：mem 名 -> [地址线, 写数据线, 写使能线, 读数据线]
memBind = {}


def openFile():
    global srcText
    if len(sys.argv) < 2:
        print("Usage: python HWCompiler.py <Source File Path>")
        sys.exit(1)
    if os.path.exists(sys.argv[1]):
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            srcText = f.read()
            srcText = srcText.replace("\n", "").replace("\r", "")
        print("Source File Loaded")
    else:
        print(f"File {sys.argv[1]} does not exist")
        sys.exit(1)


def skipBlank():
    global srcPtr
    while srcPtr < len(srcText) and (srcText[srcPtr] == " " or srcText[srcPtr] == "\t"):
        srcPtr += 1


def parseToAST():
    global srcPtr, bracketsExm
    currentExp = []
    currentAtom = ""
    while srcPtr < len(srcText):
        skipBlank()
        if srcPtr >= len(srcText):
            break
        c = srcText[srcPtr]

        if c == "(":
            srcPtr += 1
            bracketsExm += 1
            currentExp.append(parseToAST())

        elif c == ")":
            srcPtr += 1
            bracketsExm -= 1
            return currentExp

        elif c == "[":
            # 数组访问语法: var[index]
            # 解析后产生节点: ["array", var, index]
            srcPtr += 1
            bracketsExm += 1
            if not currentExp:
                print(f"Error: Unexpected '[' without array name at position {srcPtr}")
                sys.exit(1)
            arrName = currentExp.pop()
            skipBlank()
            if srcPtr >= len(srcText):
                print("Error: Unexpected end of file inside array access")
                sys.exit(1)

            if srcText[srcPtr] == "(":
                # 带括号的索引表达式: var[(expr)]
                srcPtr += 1
                bracketsExm += 1
                indexNode = parseToAST()
                skipBlank()
                if srcPtr >= len(srcText) or srcText[srcPtr] != "]":
                    print("Error: Expected ']' after array index expression")
                    sys.exit(1)
                srcPtr += 1
                bracketsExm -= 1
                currentExp.append(["array", arrName, indexNode])
            else:
                # 单 token 索引: var[32] 或 var[bitIndex]
                idxAtom = ""
                while srcPtr < len(srcText) and srcText[srcPtr] not in " \t)]":
                    idxAtom += srcText[srcPtr]
                    srcPtr += 1
                skipBlank()
                if srcPtr >= len(srcText) or srcText[srcPtr] != "]":
                    print(f"Error: Expected ']' after array index '{idxAtom}'")
                    sys.exit(1)
                srcPtr += 1
                bracketsExm -= 1
                currentExp.append(["array", arrName, idxAtom])

        elif c == "]":
            print(f"Error: Unexpected ']' at position {srcPtr}")
            sys.exit(1)

        elif c == '"':
            currentAtom = ""
            srcPtr += 1
            while srcPtr < len(srcText) and srcText[srcPtr] != '"':
                currentAtom += srcText[srcPtr]
                srcPtr += 1
            srcPtr += 1
            currentExp.append(currentAtom.replace(" ", "").replace("\t", ""))

        else:
            currentAtom = ""
            while srcPtr < len(srcText) and srcText[srcPtr] not in "()[] \t":
                currentAtom += srcText[srcPtr]
                srcPtr += 1
            currentExp.append(currentAtom)

    if bracketsExm:
        print("Warning: Brackets not matched sufficiently")
    return currentExp


def initVariables():
    global AST, memAry, regAry, wireAry, tempAry, tempInit, memBind
    description = []
    emit = 0
    for sub in AST:
        if sub[0] == "mem":
            # 每个存储体三个字段：名字 深度 位宽（例如 program 16 16）
            for i in range(1, len(sub)):
                if not isinstance(sub[i], list):
                    description.append(sub[i])
                    if len(description) == 3:
                        memAry.append(description)
                        description = []
        elif sub[0] == "reg":
            for i in range(1, len(sub)):
                if not isinstance(sub[i], list):
                    if emit:
                        description.append(sub[i])
                        regAry.append(description)
                        description = []
                        emit = 0
                    else:
                        subX = sub[i].split(":")
                        description.append(subX[0])
                        description.append(subX[1] if len(subX) > 1 else "1")
                        emit = 1
        elif sub[0] == "wire":
            for i in range(1, len(sub)):
                if not isinstance(sub[i], list):
                    if emit:
                        description.append(sub[i])
                        wireAry.append(description)
                        description = []
                        emit = 0
                    else:
                        subX = sub[i].split(":")
                        description.append(subX[0])
                        description.append(subX[1] if len(subX) > 1 else "1")
                        emit = 1
        elif sub[0] == "bind":
            # 存储体接口绑定：名字 + 四根线（地址 / 写数据 / 写使能 / 读数据）
            fields = [x for x in sub[1:] if not isinstance(x, list)]
            if len(fields) != 5:
                print("Error: bind 的写法是 " "(bind <存储体> <地址线> <写数据线> <写使能线> <读数据线>)")
                sys.exit(1)
            if fields[0] in memBind:
                print(f"Error: 存储体 {fields[0]} 重复 bind")
                sys.exit(1)
            memBind[fields[0]] = fields[1:]
        elif sub[0] == "temp":
            for i in range(1, len(sub)):
                if not isinstance(sub[i], list):
                    description.append(sub[i])
                    if emit == 0:
                        emit = 1
                    else:
                        tempAry.append(description)
                        description = []
                        emit = 0
    # 记录 temp 的声明初值，供每次展开前复位
    tempInit = [list(t) for t in tempAry]


def resetTemps():
    """把 temp 变量恢复到声明初值：每次展开循环都要从同一个起点开始。"""
    for name, val in tempInit:
        setTempByName(name, val)


def widthOf(name):
    """取信号声明位宽；未声明返回 None。"""
    for v in regAry + wireAry:
        if v[0] == name:
            return int(v[1])
    return None


def markBit(store, name, bit):
    store.setdefault(name, set()).add(bit)


def markRange(store, name, start, width):
    for k in range(width):
        markBit(store, name, start + k)


def renderRun(name, lo, hi):
    """把一段连续位渲染成 BLIF 端口写法：只含第 0 位时写裸名，其余写 [高位:低位]。"""
    if hi == 0:
        return name
    if lo == 0:
        return f"{name}[{hi}:0]"
    if lo == hi:
        return f"{name}[{hi}]"
    return f"{name}[{hi}:{lo}]"


def renderPorts(store, exclude=None):
    """按声明顺序把 {信号名: 位集合} 渲染成端口串；同一信号的非连续位拆成多段。"""
    parts = []
    for v in regAry + wireAry:
        name = v[0]
        bits = set(store.get(name, ()))
        if exclude:
            bits -= exclude.get(name, set())
        if not bits:
            continue
        ordered = sorted(bits)
        lo = prev = ordered[0]
        for b in ordered[1:] + [None]:
            if b is not None and b == prev + 1:
                prev = b
                continue
            parts.append(renderRun(name, lo, prev))
            if b is not None:
                lo = prev = b
    return " ".join(parts)


def parseLiteral(atom):
    """解析 !{宽度:值} 常量，返回 MSB 在前的比特列表；后面可继续接 0/1 逐位拼接。"""
    bits = []
    i = 0
    while i < len(atom):
        c = atom[i]
        if c == "!" and i + 1 < len(atom) and atom[i + 1] == "{":
            j = atom.find("}", i)
            if j < 0:
                print(f"Error: 位串常量缺少右花括号：{atom}")
                sys.exit(1)
            body = atom[i + 2 : j]
            if ":" not in body:
                print(f"Error: 位串常量应写作 !{{宽度:值}}：{atom}")
                sys.exit(1)
            widthText, valText = body.split(":", 1)
            if not widthText.isdigit() or not valText.isdigit():
                print(f"Error: 位串常量应写作十进制数字 !{{宽度:值}}：{atom}")
                sys.exit(1)
            width = int(widthText)
            value = int(valText)
            if width <= 0:
                print(f"Error: 位串常量的宽度必须大于 0：{atom}")
                sys.exit(1)
            if value >= (1 << width):
                print(f"Error: 位串常量 {value} 装不进 {width} 位：{atom}")
                sys.exit(1)
            # 展开成 MSB 在前的二进制位
            bits.extend(int(b) for b in format(value, f"0{width}b"))
            i = j + 1
        elif c in "01":
            bits.append(int(c))
            i += 1
        else:
            print(f"Error: 无法解析的位串常量：{atom}")
            sys.exit(1)
    return bits


def parseCaseCond(cond):
    """解析 cases 的分支条件：0 表示指示寄存器第 0 位为 1，~0 表示该位为 0。
    返回 [位下标, 要求值]。"""
    want = 1
    text = cond
    if isinstance(text, str) and text.startswith("~"):
        want = 0
        text = text[1:]
    if not isNum(text):
        print(f"Error: cases 分支条件应写作 0 或 ~0，实际是 {cond}")
        sys.exit(1)
    return [int(text), want]


def constDecl(value):
    """取 0/1 常量信号的名字；同一个 model 内只声明一次。
    返回 [信号名, 首次出现时附带的声明片段]。"""
    name = f"const{value}[0]"
    if value in constDeclared:
        return [name, ""]
    constDeclared[value] = True
    return [name, f".names {name}\n" + ("1\n" if value else "")]


def getValAndLenByName(name, vlist, getLen=0):
    if isNum(name):
        return [name]
    for i in vlist:
        if i[0] == name:
            if getLen:
                return [i[1], i[2]]
            else:
                return [i[1]]
    print(f"Error: No such variable: {name}")
    sys.exit(1)


def setTempByName(name, val):
    global tempAry
    for i in tempAry:
        if i[0] == name:
            i[1] = val
            return
    print(f"Error: No such temp variable: {name}")
    sys.exit(1)


def getMap(operation):
    global jsonMap
    opList = jsonMap["operations"]
    for op in opList:
        if op["symbol"] == operation:
            return op["TruthTable"]
    print(f"Error: No such mapping: {operation}")
    sys.exit(1)


def isNum(s):
    if not isinstance(s, str) or s == "":
        return 0
    for i in s:
        if i < "0" or i > "9":
            return 0
    return 1


def turnVarToBLIF(node):
    """把变量引用节点转成 [名字, 已求值下标, "名字[下标]"]。"""
    if isinstance(node, list) and len(node) >= 3 and node[0] == "array":
        varName = node[1]
        idxVal = evalArg(node[2])
        return [varName, idxVal, f"{varName}[{idxVal}]"]
    elif isinstance(node, str):
        if isNum(node):
            # 裸常量（一般只出现在 mapping 输入侧）
            return [node, 0, node]
        else:
            # 没写下标的普通变量，按第 0 位处理
            return [node, 0, f"{node}[0]"]
    else:
        print(f"Error: Invalid variable reference: {node}")
        sys.exit(1)


def evalArg(node):
    """把 AST 节点或变量名求值为 int。"""
    r = handleFuncs(node)
    if isinstance(r, list):
        return int(r[0])
    if isinstance(r, bool):
        return int(r)
    if r is None or r == "":
        return 0
    return int(r)


def genMapping(AST):
    global wireAry, regAry
    truthTable = getMap(AST[1])
    truthTableTTL = len(truthTable[0][0]) + len(truthTable[0][1])
    if truthTableTTL != len(AST) - 2:
        print(f"Error: Param quantity of truth table not matched: {AST[1]}")
        sys.exit(1)

    args = []
    for i in range(2, len(AST)):
        arg = AST[i]
        # 常量输入：0/1 在 BLIF 里就是常量信号名，照抄即可
        if isinstance(arg, str) and isNum(arg):
            args.append(arg)
            continue
        if isinstance(arg, str) and arg.startswith("!"):
            print(f"Error: mapping 的输入暂不支持位串常量：{arg}")
            sys.exit(1)
        transfered = turnVarToBLIF(arg)
        examine = getValAndLenByName(transfered[0], wireAry + regAry, 1)
        width = int(examine[0])
        if width > transfered[1] >= 0:
            args.append(transfered[2])
        else:
            print(
                f"Error: Variable index out bound: {transfered[0]}[{transfered[1]}] (width {width})"
            )
            sys.exit(1)

    outCount = len(truthTable[0][1])
    inArgs = args[: len(args) - outCount]
    outArgs = args[len(args) - outCount :]

    # 端口登记：输入侧记“读”，输出侧记“组合输出”（组合输出不算模块端口，只在内部使用）
    for i in range(2, len(AST) - outCount):
        arg = AST[i]
        if isinstance(arg, str) and isNum(arg):
            continue
        t = turnVarToBLIF(arg)
        markBit(portRead, t[0], t[1])
    for i in range(len(AST) - outCount, len(AST)):
        arg = AST[i]
        if isinstance(arg, str) and isNum(arg):
            continue
        t = turnVarToBLIF(arg)
        markBit(portMapOut, t[0], t[1])

    # 外层 cases 的条件作为额外输入接进来：只列出条件成立的那些行，
    # 条件不成立时该映射没有行命中，输出即恒为 0
    condNames = [f"{c[0]}[{c[1]}]" for c in caseStack]
    condBits = "".join(str(c[2]) for c in caseStack)
    for c in caseStack:
        markBit(portRead, c[0], c[1])

    tmpBLIF = ".names " + " ".join(inArgs + condNames + outArgs) + "\n"
    for i in truthTable:
        inputs = "".join(str(x) for x in i[0])
        outputs = "".join(str(x) for x in i[1])
        tmpBLIF += f"{inputs}{condBits} {outputs}\n"
    return tmpBLIF


def genLatch(AST):
    tmpBLIF = ""
    if len(AST) < 4:
        print("Error: mov param not sufficient")
        sys.exit(1)
    bitWidth = evalArg(AST[1])
    Op1 = turnVarToBLIF(AST[2])
    # 目标只可能是 reg（.latch 的寄存器）！！！
    t1 = int(getValAndLenByName(Op1[0], regAry, 1)[0])
    i1 = Op1[1]
    if i1 < 0 or i1 + bitWidth > t1:
        print(
            f"Error: mov width out bound: {Op1[0]}[{i1}..{i1 + bitWidth - 1}] (width {t1})"
        )
        sys.exit(1)

    srcNode = AST[3]
    # 常量源：!{宽度:值}，按位接到 0/1 常量信号上
    if isinstance(srcNode, str) and srcNode.startswith("!"):
        bits = parseLiteral(srcNode)
        if len(bits) > bitWidth:
            print(f"Error: 位串常量 {srcNode} 有 {len(bits)} 位，装不进 {bitWidth} 位的 mov")
            sys.exit(1)
        for k in range(bitWidth):
            bitVal = bits[len(bits) - 1 - k] if k < len(bits) else 0
            name, decl = constDecl(bitVal)
            tmpBLIF += decl
            tmpBLIF += f".latch {name} {Op1[0]}[{i1 + k}] re\n"
        markRange(portMovOut, Op1[0], i1, bitWidth)
        return tmpBLIF

    Op2 = turnVarToBLIF(srcNode)
    # 源既可能是 wire（组合结果），也可能是 reg
    t2 = int(getValAndLenByName(Op2[0], wireAry + regAry, 1)[0])
    i2 = Op2[1]
    if i2 < 0 or i2 + bitWidth > t2:
        print(
            f"Error: mov source width out bound: {Op2[0]}[{i2}..{i2 + bitWidth - 1}] (width {t2})"
        )
        sys.exit(1)

    markRange(portMovOut, Op1[0], i1, bitWidth)
    markRange(portRead, Op2[0], i2, bitWidth)
    # BLIF 的 .latch 写作 <输入 D> <输出 Q>，而 mov 是 (mov 宽度 寄存器 下一状态)
    for k in range(0, bitWidth):
        tmpBLIF += f".latch {Op2[0]}[{i2 + k}] {Op1[0]}[{i1 + k}] re\n"
    return tmpBLIF


def genParallel(AST):
    """(parallel (条件) 体...)：条件成立就展开一轮，把体里的表达式生成出来。
    计数器必须由循环体自己推进，例如 (var bitIndex (add bitIndex 1))。"""
    tmpBLIF = ""
    condNode = AST[1]
    if not isinstance(condNode, list) or len(condNode) < 2:
        print(f"Error: parallel 的条件不合法：{condNode}")
        sys.exit(1)
    condName = condNode[1]
    rounds = 0
    while evalArg(condNode):
        before = evalArg(condName)
        for i in range(2, len(AST)):
            r = handleFuncs(AST[i])
            if isinstance(r, str):
                tmpBLIF += r
        if evalArg(condName) == before:
            print(
                f"Error: parallel 的计数器 {condName} 没有被推进，会死循环；"
                f"循环体末尾应写 (var {condName} (add {condName} 1))"
            )
            sys.exit(1)
        rounds += 1
        if rounds > 65536:
            print(f"Error: parallel 展开次数异常（计数器 {condName}）")
            sys.exit(1)
    return tmpBLIF


def genCases(AST):
    """(cases 指示寄存器 条件 (体) ...)：条件写作 0/~0 表示该位为 1/0，
    分支内的 mapping 会把这些条件并进真值行，嵌套时逐层叠加。"""
    if len(AST) < 4:
        print("Error: cases 至少需要一个分支")
        sys.exit(1)
    regName = AST[1]
    width = widthOf(regName)
    if width is None:
        print(f"Error: cases 的指示寄存器 {regName} 未声明")
        sys.exit(1)
    tmpBLIF = ""
    for i in range(2, len(AST), 2):
        if i + 1 >= len(AST):
            print(f"Error: cases 分支缺少分支体：{AST[i]}")
            sys.exit(1)
        bit, want = parseCaseCond(AST[i])
        if not 0 <= bit < width:
            print(f"Error: cases 条件 {AST[i]} 超出 {regName} 的位宽 {width}")
            sys.exit(1)
        markBit(portRead, regName, bit)
        caseStack.append([regName, bit, want])
        body = AST[i + 1]
        if not isinstance(body, list):
            print(f"Error: cases 分支体必须是列表：(体...)")
            sys.exit(1)
        for item in body:
            r = handleFuncs(item)
            if isinstance(r, str):
                tmpBLIF += r
        caseStack.pop()
    return tmpBLIF


def genMux(AST):
    """(mux (条件...) (输出wire...) (条件值... 源...) ...)
    每支把条件逐个与给出的值比较，全中则该支成立，把源按位送到输出。条件值可以写：
      数字           等于该值
      (v1 v2 ...)    命中其中任意一个（多值集合）
      -              通配，该条件不参与判断
    源比输出窄的高位补 0、宽的截断；各支或起来，没有任何一支成立时输出 0；
    (- 源...) 是默认分支，所有支都不中时生效。"""
    if len(AST) < 4:
        print("Error: mux 至少需要一个分支")
        sys.exit(1)
    conds, outs = AST[1], AST[2]
    if not isinstance(conds, list) or not isinstance(outs, list):
        print("Error: mux 前两个参数必须是列表：(条件...) (输出wire...)")
        sys.exit(1)
    for name in conds + outs:
        if widthOf(name) is None:
            print(f"Error: mux 的信号 {name} 未声明")
            sys.exit(1)
    nCond = len(conds)
    global muxSeq
    muxSeq += 1
    tag = f"mux{muxSeq}_"
    tmpBLIF = ""
    sels, srcs = [], []
    defaultSrcs = None
    wildcards = 0
    for k in range(3, len(AST)):
        case = AST[k]
        if isinstance(case, list) and case and case[0] == "#":
            continue
        if not isinstance(case, list) or not case:
            print(f"Error: mux 分支不合法：{case}")
            sys.exit(1)
        # 默认分支：- 后面只跟源（写一个源就广播给所有输出）
        if case[0] == "-" and len(case) in (2, 1 + len(outs)):
            if defaultSrcs is not None:
                print("Error: mux 只能有一个默认分支")
                sys.exit(1)
            defaultSrcs = case[1:]
            continue
        if len(case) < nCond + 1:
            print(f"Error: mux 分支 {case} 需要 {nCond} 个条件值和一个源")
            sys.exit(1)
        # 本支的条件值：数字 / 值列表 / 通配 -
        valueSets = []
        for c, v in zip(conds, case[:nCond]):
            w = widthOf(c)
            if v == "-":
                valueSets.append(None)
                continue
            vals = v if isinstance(v, list) else [v]
            got = []
            for one in vals:
                if not isinstance(one, str) or not one.isdigit():
                    print(f"Error: mux 条件值必须是十进制数字、值列表或 -：{v}")
                    sys.exit(1)
                if int(one) >= (1 << w):
                    print(f"Error: mux 条件值 {one} 超出 {c} 的位宽 {w}")
                    sys.exit(1)
                got.append(int(one))
            markRange(portRead, c, 0, w)
            valueSets.append(got)
        branch = case[nCond:]
        if len(branch) == 1:
            branch *= len(outs)
        if len(branch) != len(outs):
            print(f"Error: mux 分支 {case} 的源个数应为 {len(outs)}")
            sys.exit(1)
        for s in branch:
            if widthOf(s) is None:
                print(f"Error: mux 的源 {s} 未声明")
                sys.exit(1)
        if all(v is None for v in valueSets):
            # 条件值全写成通配（如 (- - pc)）：语义就是"所有条件都不看"，
            # 当成默认分支处理，否则它会变成恒定命中的一支并和其它支按位或
            if defaultSrcs is not None:
                print("Error: mux 只能有一个默认分支")
                sys.exit(1)
            defaultSrcs = branch
            continue
        # 选中信号：全是单值且无通配时就是一个最小项，否则按"每个条件命中"求与
        sel = f"{tag}sel{k - 3}"
        if all(v is not None and len(v) == 1 for v in valueSets):
            names, pattern = [], ""
            for c, v in zip(conds, valueSets):
                w = widthOf(c)
                names += [f"{c}[{b}]" for b in range(w)]
                pattern += format(v[0], f"0{w}b")[::-1]  # 表头按下标递增，模式低位在前
            tmpBLIF += ".names " + " ".join(names) + f" {sel}\n{pattern} 1\n"
        else:
            hits = []
            for i, (c, v) in enumerate(zip(conds, valueSets)):
                if v is None:
                    wildcards += 1  # 通配：该条件不参与判断
                    continue
                w = widthOf(c)
                bits = [f"{c}[{b}]" for b in range(w)]
                terms = []
                for j, val in enumerate(v):
                    mt = f"{tag}mt{k - 3}_{i}_{j}"
                    tmpBLIF += (
                        ".names "
                        + " ".join(bits)
                        + f" {mt}\n{format(val, f'0{w}b')[::-1]} 1\n"
                    )
                    terms.append(mt)
                if len(terms) == 1:
                    hits.append(terms[0])
                else:
                    hs = f"{tag}has{k - 3}_{i}"
                    tmpBLIF += ".names " + " ".join(terms) + f" {hs}\n"
                    for j in range(len(terms)):
                        tmpBLIF += "-" * j + "1" + "-" * (len(terms) - j - 1) + " 1\n"
                    hits.append(hs)
            if not hits:  # 所有条件都通配：这一支恒真
                tmpBLIF += f".names {sel}\n1\n"
            elif len(hits) == 1:
                tmpBLIF += f".names {hits[0]} {sel}\n1 1\n"
            else:
                tmpBLIF += (
                    ".names " + " ".join(hits) + f" {sel}\n" + "1" * len(hits) + " 1\n"
                )
        sels.append(sel)
        srcs.append(branch)
    # 默认分支：所有支的选中信号都为 0 时生效
    if defaultSrcs is not None:
        branch = defaultSrcs
        if len(branch) == 1:
            branch *= len(outs)
        if len(branch) != len(outs):
            print(f"Error: mux 默认分支的源个数应为 {len(outs)}")
            sys.exit(1)
        for s in branch:
            if widthOf(s) is None:
                print(f"Error: mux 的源 {s} 未声明")
                sys.exit(1)
        if sels:
            tmpBLIF += ".names " + " ".join(sels) + f" {tag}any_case\n"
            for i in range(len(sels)):
                tmpBLIF += "-" * i + "1" + "-" * (len(sels) - i - 1) + " 1\n"
            tmpBLIF += f".names {tag}any_case {tag}no_case\n0 1\n"
        else:
            tmpBLIF += f".names {tag}no_case\n1\n"
        sels.append(f"{tag}no_case")
        srcs.append(branch)
    if wildcards and len(sels) > 1:
        print(
            f"Warning: mux 有条件位写成通配 -，可能与其它分支重叠；"
            f"多支同时命中时输出是各源的按位或，通配分支通常应写成默认分支 (- 源)"
        )
    # 每一位：本位的选中信号与各支源位组成一个 .names，逐支给一行
    for j, o in enumerate(outs):
        ow = widthOf(o)
        markRange(portMapOut, o, 0, ow)
        for b in range(ow):
            used, srcBits = [], []
            for k in range(len(sels)):
                s = srcs[k][j]
                if b < widthOf(s):
                    used.append(k)
                    if s not in srcBits:
                        srcBits.append(s)
            if not used:
                tmpBLIF += f".names {o}[{b}]\n"  # 没有分支覆盖这一位，恒 0
                continue
            names = [sels[k] for k in used] + [f"{s}[{b}]" for s in srcBits]
            tmpBLIF += ".names " + " ".join(names) + f" {o}[{b}]\n"
            for i, k in enumerate(used):
                row = ["0"] * len(used) + ["-"] * len(srcBits)
                row[i] = "1"
                row[len(used) + srcBits.index(srcs[k][j])] = "1"
                tmpBLIF += "".join(row) + " 1\n"
            for s in srcBits:
                markBit(portRead, s, b)
    return tmpBLIF


def generateMuxModel(muxAST):
    """顶层 mux 单独成一个 model：条件与源作输入，第二个参数列表作输出。"""
    global portRead, portMapOut, portMovOut, constDeclared
    portRead = {}
    portMapOut = {}
    portMovOut = {}
    constDeclared = {}
    resetTemps()
    body = genMux(muxAST)
    written = {}
    for store in (portMapOut, portMovOut):
        for name, bits in store.items():
            written.setdefault(name, set()).update(bits)
    inputs = renderPorts(portRead, exclude=written)
    outputs = " ".join(renderRun(o, 0, widthOf(o) - 1) for o in muxAST[2])
    return f".model mux_{muxAST[2][0]}\n.inputs {inputs}\n.outputs {outputs}\n{body}.end\n\n"


def checkMemBind(memName, depth, width, bind):
    """校验 (bind …) 指定的四根线：必须在 wire 段声明，且宽度与存储体匹配。"""
    abits = max(1, (depth - 1).bit_length())
    want = [("地址线", abits), ("写数据线", width), ("写使能线", 1), ("读数据线", width)]
    for (role, w), sig in zip(want, bind):
        if any(v[0] == sig for v in regAry):
            print(f"Error: bind 的{role} {sig} 是 reg；存储体接口只能用 wire")
            sys.exit(1)
        if widthOf(sig) is None:
            print(f"Error: bind 的{role} {sig} 没有在 wire 段声明")
            sys.exit(1)
        if int(widthOf(sig)) != w:
            print(
                f"Error: bind 的{role} {sig} 是 {widthOf(sig)} 位，"
                f"存储体 {memName}（{depth} 字 × {width} 位）需要 {w} 位"
            )
            sys.exit(1)


def genMemModel(name, depth, width, mode, bind):
    """生成一个存储体的 model：端口名由 (bind …) 给出，不再靠命名约定。
    读数据打一拍，两种实现时序一致；mode 为 subckt 时只发一个黑盒实例交给 BSRAM。"""
    abits = max(1, (depth - 1).bit_length())
    addrName, dinName, weName, doutName = bind
    addr = renderRun(addrName, 0, abits - 1)
    dat = renderRun(dinName, 0, width - 1)
    out = renderRun(doutName, 0, width - 1)
    head = f".model mem_{name}\n.inputs {addr} {dat} {weName}\n.outputs {out}\n"
    if mode == "subckt":
        return (
            head
            + f".subckt RAM{depth}x{width} A={addr} D={dat} WE={weName} Q={out}\n.end\n\n"
        )
    body = ""
    # 字线译码：地址最小项
    for w in range(depth):
        ins = " ".join(f"{addrName}[{b}]" for b in range(abits))
        body += f".names {ins} {name}_wl{w}\n{format(w, f'0{abits}b')[::-1]} 1\n"
    # 写：命中字线且有写使能时取 din，否则保持
    for w in range(depth):
        for b in range(width):
            cell = f"{name}_{w}_{b}"
            body += f".names {name}_wl{w} {weName} {dinName}[{b}] {cell} {cell}_next\n"
            body += "111- 1\n00-1 1\n01-1 1\n10-1 1\n"
            body += f".latch {cell}_next {cell} re\n"
    # 读：各字数据位按字线或起来，打一拍输出
    for b in range(width):
        ins = " ".join(f"{name}_wl{w} {name}_{w}_{b}" for w in range(depth))
        body += f".names {ins} {name}_rd{b}\n"
        for w in range(depth):
            row = ["-"] * (depth * 2)
            row[w * 2] = row[w * 2 + 1] = "1"
            body += "".join(row) + " 1\n"
        body += f".latch {name}_rd{b} {doutName}[{b}] re\n"
    return head + body + ".end\n\n"


def addCall(AST):
    first = evalArg(AST[1])
    for i in range(2, len(AST)):
        first += evalArg(AST[i])
    return first


def subCall(AST):
    first = evalArg(AST[1])
    for i in range(2, len(AST)):
        first -= evalArg(AST[i])
    return first


def mulCall(AST):
    first = evalArg(AST[1])
    for i in range(2, len(AST)):
        first *= evalArg(AST[i])
    return first


def divCall(AST):
    first = evalArg(AST[1])
    for i in range(2, len(AST)):
        first //= evalArg(AST[i])
    return first


def eqCall(AST):
    return int(evalArg(AST[1]) == evalArg(AST[2]))


def ltCall(AST):
    return int(evalArg(AST[1]) < evalArg(AST[2]))


def gtCall(AST):
    return int(evalArg(AST[1]) > evalArg(AST[2]))


def leCall(AST):
    return int(evalArg(AST[1]) <= evalArg(AST[2]))


def geCall(AST):
    return int(evalArg(AST[1]) >= evalArg(AST[2]))


def neCall(AST):
    return int(evalArg(AST[1]) != evalArg(AST[2]))


def andCall(AST):
    first = evalArg(AST[1])
    for i in range(2, len(AST)):
        first = first and evalArg(AST[i])
    return int(first)


def orCall(AST):
    first = evalArg(AST[1])
    for i in range(2, len(AST)):
        first = first or evalArg(AST[i])
    return int(first)


def notCall(AST):
    return int(not evalArg(AST[1]))


def setvar(AST):
    setTempByName(AST[1], evalArg(AST[2]))
    return ""


def handleFuncs(AST):
    if not isinstance(AST, list):
        return getValAndLenByName(AST, tempAry)[0]
    if not AST:
        return ""
    # 注释 (# ...) 不参与生成
    if AST[0] == "#":
        return ""
    if AST[0] == "mapping":
        return genMapping(AST)
    elif AST[0] == "mov":
        return genLatch(AST)
    elif AST[0] == "parallel":
        return genParallel(AST)
    elif AST[0] == "add":
        return addCall(AST)
    elif AST[0] == "sub":
        return subCall(AST)
    elif AST[0] == "mul":
        return mulCall(AST)
    elif AST[0] == "div":
        return divCall(AST)
    elif AST[0] == "eq":
        return eqCall(AST)
    elif AST[0] == "lt":
        return ltCall(AST)
    elif AST[0] == "gt":
        return gtCall(AST)
    elif AST[0] == "le":
        return leCall(AST)
    elif AST[0] == "ge":
        return geCall(AST)
    elif AST[0] == "ne":
        return neCall(AST)
    elif AST[0] == "and":
        return andCall(AST)
    elif AST[0] == "or":
        return orCall(AST)
    elif AST[0] == "not":
        return notCall(AST)
    elif AST[0] == "var":
        return setvar(AST)
    elif AST[0] == "cases":
        return genCases(AST)
    elif AST[0] == "mux":
        return genMux(AST)
    else:
        print(f"Error: Unknown operation: {AST[0]}")
        sys.exit(1)


def generateBLIF(instAST):
    """生成一个 inst 的 .model：.inputs 取被读到而本模块没驱动的位，.outputs 取 mov 的目标。"""
    global memAry, regAry, wireAry, tempAry
    global portRead, portMapOut, portMovOut, constDeclared
    portRead = {}
    portMapOut = {}
    portMovOut = {}
    constDeclared = {}
    resetTemps()
    body = ""
    for i in range(2, len(instAST)):
        r = handleFuncs(instAST[i])
        if isinstance(r, str):
            body += r
    # 被本模块驱动的位：mapping 的输出 + mov 的目标
    written = {}
    for store in (portMapOut, portMovOut):
        for name, bits in store.items():
            written.setdefault(name, set()).update(bits)
    inputs = renderPorts(portRead, exclude=written)
    outputs = renderPorts(portMovOut)
    BLIF = f".model {instAST[1]}\n"
    BLIF += f".inputs {inputs}\n"
    BLIF += f".outputs {outputs}\n"
    BLIF += body
    BLIF += ".end\n\n"
    return BLIF


def muxModelName(muxAST):
    return f"mux_{muxAST[2][0]}"


def modelDrivers(text):
    """扫出 model 驱动的信号位：.names 的输出段 + .latch 的输出（第二个参数）。"""
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    driven = set()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith(".names"):
            toks = ln.split()[1:]
            rows, j = [], i + 1
            while j < len(lines) and not lines[j].startswith("."):
                rows.append(lines[j])
                j += 1
            nout = 1
            if rows:
                parts = rows[0].split()
                nout = len(parts[1]) if len(parts) > 1 else 1
            driven.update(toks[len(toks) - nout :])
            i = j
            continue
        if ln.startswith(".latch"):
            toks = ln.split()[1:]
            if len(toks) > 1:
                driven.add(toks[1])
        i += 1
    return driven


def checkDrivers(models):
    """跨 model 检查：同一位只能有一个驱动源；两个 model 抢同一根线，展平后就是短路。"""
    owners, conflicts = {}, {}
    for name, text in models.items():
        for sig in modelDrivers(text):
            if sig in owners:
                conflicts.setdefault(sig, {owners[sig]}).add(name)
            else:
                owners[sig] = name
    return conflicts


def loadJson(name):
    global jsonMap
    jPath = os.path.dirname(sys.argv[1]) + f"\\{name}"
    if os.path.exists(jPath):
        with open(jPath, "r", encoding="utf-8") as f:
            jsonMap = json.load(f)
        print("Json file Loaded")
    else:
        print(f"Json file {jPath} does not exist")
        sys.exit(1)


def saveFile(BLIF):
    name = (
        os.path.dirname(sys.argv[1])
        + f"\\{os.path.splitext(os.path.basename(sys.argv[1]))[0]}"
        + ".BLIF"
    )
    with open(name, "w", encoding="utf-8") as f:
        f.write(BLIF)
    print(f"File {name} generated successfully")


if __name__ == "__main__":
    openFile()
    loadJson("TruthTable.json")
    AST = parseToAST()
    print(AST)
    initVariables()
    m = r = w = 0
    for i in memAry:
        m += int(i[1]) * int(i[2])
    for j in wireAry:
        w += int(j[1])
    for k in regAry:
        r += int(k[1])
    if "--subckt" in sys.argv:
        memMode = "subckt"
    print(f"存储体实现方式：{memMode}")
    models = {}
    for item in AST:
        if item[0] == "inst":
            models[item[1]] = generateBLIF(item)
        elif item[0] == "mux":
            models[muxModelName(item)] = generateMuxModel(item)
    for bindName in memBind:
        if not any(m[0] == bindName for m in memAry):
            print(f"Error: bind 引用了未声明的存储体 {bindName}")
            sys.exit(1)
    for memName, memDepth, memWidth in memAry:
        if memName not in memBind:
            print(
                f"Error: 存储体 {memName} 没有 bind；请写 "
                f"(bind {memName} <地址线> <写数据线> <写使能线> <读数据线>)"
            )
            sys.exit(1)
        checkMemBind(memName, int(memDepth), int(memWidth), memBind[memName])
        bAddr, bDin, bWe, bDout = memBind[memName]
        print(
            f"存储体 {memName}: {memDepth} 字 × {memWidth} 位 = "
            f"{int(memDepth) * int(memWidth)} bit，绑定 "
            f"{bAddr} / {bDin} / {bWe} / {bDout}"
        )
        models[f"mem_{memName}"] = genMemModel(
            memName, int(memDepth), int(memWidth), memMode, memBind[memName]
        )
    conflicts = checkDrivers(models)
    if conflicts:
        print(f"Warning: {len(conflicts)} 个信号位被多个 model 驱动（展平后会短路）：")
        for sig, who in sorted(conflicts.items())[:12]:
            print(f"  {sig} <- {'/'.join(sorted(who))}")
    g = f"# Memory: {m}, Reg: {r}, Wire: {w}\n" + "".join(models.values())
    saveFile(g)
