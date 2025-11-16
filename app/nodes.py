import os, json, subprocess, glob, re
from typing import TypedDict, List, Optional, Dict, Any
from app.llm import LLMClient

class State(TypedDict, total=False):
    """
    全局状态类型，贯穿流程图各节点进行传递与累积。

    作用：聚合请求、代码上下文、计划与设计、生成/修复结果、提交信息以及服务运行状态。
    约束：total=False 允许字段按需填充，各节点只需返回自己产出的增量字段。
    """
    request: str  # 用户请求/指令
    repo_path: str  # 仓库根路径
    service_entry: Optional[str]  # 服务入口（如 'app.main:app' 或自定义命令）
    type: str  # 任务类型：dev/bugfix/qna
    plan: str  # 开发/修复计划文本
    design: str  # 系统设计要点与模块划分
    code_changes: List[Dict[str, Any]]  # 代码变更描述（可选）
    logs: str  # 日志内容（用于定位问题）
    files_to_save: List[Dict[str, str]]  # 需要写入到项目的文件列表
    commit_message: str  # Git 提交信息
    service_started: bool  # 服务是否已启动
    console_errors: str  # 控制台错误摘要
    done: bool  # 流程是否结束
    message: str  # 通用消息/上下文
    install_commands: List[str]  # 依赖安装命令序列
    start_command: Optional[str]  # 启动服务命令

llm = LLMClient()  # 使用 OpenRouter 的 OpenAI SDK 封装

def _detect_service_commands(rp: str):
    # 基于仓库内容自动推断依赖安装与启动命令（兜底策略）
    install: List[str] = []
    start: Optional[str] = None
    if os.path.exists(os.path.join(rp, "package.json")):
        install = ["npm install"]
        start = "npm run dev"
    elif os.path.exists(os.path.join(rp, "pom.xml")) or os.path.exists(os.path.join(rp, "build.gradle")):
        if os.path.exists(os.path.join(rp, "mvnw.cmd")):
            install = ["mvnw.cmd -q -DskipTests package"]
            start = "mvnw.cmd spring-boot:run"
        elif os.path.exists(os.path.join(rp, "gradlew.bat")):
            install = ["gradlew.bat build -x test"]
            start = "gradlew.bat bootRun"
        else:
            install = ["mvn -q -DskipTests package"]
            start = "mvn spring-boot:run"
    elif os.path.exists(os.path.join(rp, "composer.json")):
        install = ["composer install"]
        if os.path.exists(os.path.join(rp, "artisan")):
            start = "php artisan serve --host 0.0.0.0 --port 8000"
        elif os.path.isdir(os.path.join(rp, "public")):
            start = "php -S 0.0.0.0:8000 -t public"
        elif os.path.exists(os.path.join(rp, "index.php")):
            start = "php -S 0.0.0.0:8000 index.php"
    elif os.path.exists(os.path.join(rp, "go.mod")):
        install = ["go mod tidy"]
        start = "go run ."
    elif os.path.exists(os.path.join(rp, "requirements.txt")):
        install = ["pip install -r requirements.txt"]
    return install, start

def _parse_arc_files(raw):
    files: List[Dict[str, str]] = []
    shells: List[str] = []
    if not raw:
        return files, shells
    s = str(raw)
    for m in re.finditer(r"<arc-file\s+([^>]+)>([\s\S]*?)</arc-file>", s, re.I):
        attrs = m.group(1)
        body = m.group(2)
        t = None
        p = None
        mt = re.search(r"type\s*=\s*\"(.*?)\"", attrs, re.I)
        if mt:
            t = mt.group(1).strip().lower()
        mp = re.search(r"path\s*=\s*\"(.*?)\"", attrs, re.I)
        if mp:
            p = mp.group(1).strip()
        if t == "file" and p:
            files.append({"file_path": p, "content": body})
        elif t == "shell":
            for line in body.splitlines():
                cmd = line.strip()
                if cmd:
                    shells.append(cmd)
    return files, shells

def classify_request(state: State) -> State:
    # 需求分流：返回 dev/bugfix/qna
    sys = """
    只输出一个小写词：dev 或 bugfix 或 qna，不要任何其他字符。
    判定规则：
    - dev：包含新增/实现/搭建/开发/生成/编写/创建/设计/集成/改造等意图；或要求生成文件/接口/组件/SQL/项目结构。
    - bugfix：出现报错/错误/异常/崩溃/无法运行/堆栈/错误码/日志，并意图修复或定位问题；含“修复”“报错”“报异常”“错误日志”。
    - qna：知识性或科普问答（如 原理/是什么/怎么做/区别/示例/最佳实践），不涉及落地实现或修复。
    优先级：若同时有错误日志并要求修复，选择 bugfix；若既有知识问答也包含明确开发需求，选择 dev。
    示例：
    - “开发一个 OA 系统，提供登录与审批” → dev
    - “运行时报错：Traceback ...，请修复” → bugfix
    - “什么是 CORS，如何工作？” → qna
    """
    usr = f"需求:{state.get('request')}"
    out = llm.chat(sys, usr).strip().lower()
    if out not in ["dev","bugfix","qna"]:
        out = "qna"
    print(f"type: {out}")
    return {"type": out}

def read_code(state: State) -> State:
    # 读取仓库部分文件，采样上下文作为 LLM 的提示
    rp = state["repo_path"]
    files = []
    patterns = ["**/*.py","**/*.js","**/*.ts","**/*.go","**/*.java"]
    for p in patterns:
        files += glob.glob(os.path.join(rp, p), recursive=True)
    files = files[:10]
    snippets = []
    for f in files:
        try:
            with open(f,"r",encoding="utf-8",errors="ignore") as fh:
                snippets.append({"path": f, "content": fh.read(2000)})
        except:
            pass
    ctx = json.dumps(snippets, ensure_ascii=False)
    print(f"read_code: {len(files)} files")
    return {"message": ctx}

def analyze_requirements(state: State) -> State:
    # 根据上下文与需求生成开发计划（文本）
    sys = """
    你是资深架构师。基于用户需求与仓库上下文输出简明开发计划。
    规则：
    - 用中文、分点列出，避免客套与赘述；每点不超过两行。
    - 必须包含：目标与范围、功能拆分、模块与边界、接口草案（方法/路径/请求/响应）、数据模型要点（表/字段或实体）、依赖与技术栈、落地步骤（按优先级）。
    - 不要输出工期/人员/预算/风险评估等非技术信息。
    - 即使代码上下文为空，也给出合理默认方案。
    输出格式：使用编号的列表。
    """
    usr = f"需求:{state['request']}\n代码上下文:{state.get('message','')}"
    plan = llm.chat(sys, usr, temperature=0.0)
    print(f"plan: {str(plan)}")
    return {"plan": plan}

def design_solution(state: State) -> State:
    # 根据计划生成系统设计要点（文本）
    sys = """
    你是资深架构师。基于开发计划输出系统设计要点，要求简洁且可落实。
    必须包含：
    - 架构与技术栈：框架/库/数据库/缓存/消息等选择与理由（一行即可）。
    - 模块划分：模块名与责任边界，关键输入/输出。
    - 接口草案：方法/路径/参数/请求体/响应体简述（至少列登录与一个核心功能）。
    - 数据模型要点：核心实体或表，关键字段及约束（主键/唯一/外键）。
    - 安全与治理：认证授权（如 JWT/RBAC）、CORS、速率限制、审计日志要点。
    - 配置与环境：必要的环境变量与默认值建议。
    - 目录结构建议：高层级目录与文件示例（不必详尽）。
    - 运行与运维：启动方式、健康检查、错误处理与日志策略。
    输出格式：使用编号列表，每点不超过两行，避免赘述。
    """
    des = llm.chat(sys, state["plan"], temperature=0.0)
    print(f"design: {str(des)}")
    return {"design": des}

def generate_code(state: State) -> State:
    # 生成代码文件，并尝试提取依赖安装与启动命令
    sys = """
    不要解释、不要Markdown或代码块，按下面格式输出。
    结构：
    <arc-file type=\"file\" path=\"路径/文件.ext\">文件内容</arc-file>
    <arc-file type=\"shell\">命令内容</arc-file>
    要求：
    - file：用于写入新文件或更新现有文件。
    - shell：用于安装依赖、启动项目的shell命令（多行，每行一条；最后一条视为启动命令）。
    """
    usr = f"需求:{state['request']}\n设计:{state['design']}"
    raw = llm.chat(sys, usr)
    files, shells = _parse_arc_files(raw)
    install: List[str] = []
    start: Optional[str] = None
    if shells:
        if len(shells) > 1:
            install = shells[:-1]
            start = shells[-1]
        else:
            start = shells[0]
    if not files:
        print("generate_code parse_error: invalid arc-file")
        try:
            print(f"generate_code raw: {str(raw)[:400]}")
        except:
            pass
    if not install or not start:
        # 缺少命令时进行自动推断
        det_i, det_s = _detect_service_commands(state["repo_path"])
        if not install:
            install = det_i
        if not start:
            start = det_s
    try:
        print(f"generate_code: {len(files)} files")
    except:
        pass
    return {"files_to_save": files, "commit_message": f"feat: {state['request']}", "install_commands": install, "start_command": start}

def read_logs(state: State) -> State:
    # 读取候选日志文件，用于后续定位问题
    rp = state["repo_path"]
    candidates = ["logs/app.log","app.log","logs/error.log"]
    content = ""
    for c in candidates:
        p = os.path.join(rp, c)
        if os.path.exists(p):
            try:
                with open(p,"r",encoding="utf-8",errors="ignore") as fh:
                    content = fh.read()
                    break
            except:
                pass
    print(f"read_logs: {len(content)} chars")
    return {"logs": content}

def locate_issue(state: State) -> State:
    # 根据日志与代码上下文生成修复计划（文本）
    sys = """
    你是资深排错工程师。结合错误日志与代码片段，输出可执行的修复计划。
    要求：
    - 用中文编号列出，简洁，不要赘述；每点不超过两行。
    - 必须包含：
      1) 根因分析（错误类型、触发条件、影响范围）；
      2) 受影响文件路径与原因（逐项列出）；
      3) 修复思路与具体修改点（按文件分组）；
      4) 验证方案（复现步骤、测试用例或接口/脚本）；
      5) 兼容性与回滚方案（如数据库变更、接口兼容）。
    输出严格为纯文本计划，不要代码或命令。
    """
    usr = f"日志:\n{state.get('logs','')}\n\n代码上下文采样:\n{state.get('message','')}"
    plan = llm.chat(sys, usr, temperature=0.0)
    print(f"bugfix_plan: {str(plan)}")
    return {"plan": plan}

def fix_code(state: State) -> State:
    # 按修复计划生成修复文件（arc-file 格式）
    sys = """
    只输出 arc-file 标签，不要解释、不要Markdown、不要多余文本。
    结构：
    <arc-file type=\"file\" path=\"路径/文件.ext\">完整源码</arc-file>
    规则：
    - 可多条，每条一个文件；路径相对仓库根；
    - 内容必须为完整可运行源码，避免省略号与占位；
    - 修改现有文件时直接输出整文件内容；新增文件同理；
    - 保证导入/依赖正确，语法通过；不要输出二进制或过大文件；
    - 严禁输出除 arc-file 外的任何字符。
    """
    raw = llm.chat(sys, state["plan"], temperature=0.0)
    files, _shells = _parse_arc_files(raw)
    if not files:
        print("fix_code parse_error: invalid arc-file")
        try:
            print(f"fix_code raw: {str(raw)[:400]}")
        except:
            pass
    try:
        print(f"fix_code: {len(files)} files")
    except:
        pass
    return {"files_to_save": files, "commit_message": "fix: bug修复"}

def save_files(state: State) -> State:
    # 将生成/修复的文件写入到仓库目标路径
    rp = state["repo_path"]
    saved = []
    for item in state.get("files_to_save", []):
        fp = item.get("file_path") or item.get("path")
        ct = item.get("content","")
        if not fp:
            continue
        ap = fp if os.path.isabs(fp) else os.path.join(rp, fp)
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap,"w",encoding="utf-8") as f:
            f.write(ct)
        saved.append(ap)
    print(f"save_files: {len(saved)}")
    return {"message": json.dumps(saved, ensure_ascii=False)}

def version_commit(state: State) -> State:
    # 用 Git 进行一次快照提交，便于回溯
    rp = state["repo_path"]
    try:
        if not os.path.exists(os.path.join(rp, ".git")):
            subprocess.run(["git","init"], cwd=rp, check=False)
        subprocess.run(["git","add","-A"], cwd=rp, check=False)
        subprocess.run(["git","commit","-m",state.get("commit_message","update")], cwd=rp, check=False)
    except:
        pass
    print(f"commit: {state.get('commit_message','')}")
    return {}

def service_manage(state: State) -> State:
    # 安装依赖并启动服务：优先使用模型返回的命令，其次自动推断
    rp = state["repo_path"]
    entry = state.get("service_entry")
    installs = state.get("install_commands") or []
    start = state.get("start_command")
    try:
        for c in installs:
            if isinstance(c, str) and c.strip():
                subprocess.run(c, cwd=rp, shell=True, check=False)
        cmd = None
        if start and isinstance(start, str) and start.strip():
            cmd = start
        else:
            if entry:
                if ":" in entry:
                    cmd = f"uvicorn {entry} --host 0.0.0.0 --port 8000 --reload"
                else:
                    cmd = entry
            if not cmd:
                det_i, det_s = _detect_service_commands(rp)
                for c in det_i:
                    subprocess.run(c, cwd=rp, shell=True, check=False)
                cmd = det_s
        if not cmd:
            print("service_started: False")
            return {"service_started": False}
        subprocess.Popen(cmd, cwd=rp, shell=True)
        print("service_started: True")
        return {"service_started": True}
    except:
        print("service_started: False")
        return {"service_started": False}

def check_console_errors(state: State) -> State:
    # 留空占位：可扩展为读取前端控制台或后端日志的错误
    print("console_errors: ")
    return {"console_errors": ""}

def daily_qna(state: State) -> State:
    # 问答节点：返回简要答案
    sys = "你是资深助手，简明回答开发相关问题。"
    ans = llm.chat(sys, state["request"])
    try:
        print(f"qna: {str(ans)[:400]}")
    except:
        pass
    return {"done": True, "message": ans}