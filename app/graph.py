"""LangGraph 编排

- 将流程图中的分支/汇合用有状态图表示
- 入口：`classify`，根据类型流向开发/修复/QnA
- 结束：`END` 或错误回路到日志解析
"""
from langgraph.graph import StateGraph, END
from app.nodes import (
    State, classify_request, read_code, analyze_requirements, design_solution,
    generate_code, read_logs, locate_issue, fix_code, save_files, version_commit,
    service_manage, check_console_errors, daily_qna
)

def build_app():
    """构建并编译流程图

    Returns:
        可调用的有状态应用（`.invoke(state)`）
    """
    g = StateGraph(State)
    # 节点注册：对应需求分类、开发、修复、持久化、服务管理等步骤
    g.add_node("classify", classify_request)
    g.add_node("read_code", read_code)
    g.add_node("analyze_requirements", analyze_requirements)
    g.add_node("design_solution", design_solution)
    g.add_node("generate_code", generate_code)
    g.add_node("read_logs", read_logs)
    g.add_node("locate_issue", locate_issue)
    g.add_node("fix_code", fix_code)
    g.add_node("save_files", save_files)
    g.add_node("version_commit", version_commit)
    g.add_node("service_manage", service_manage)
    g.add_node("check_console_errors", check_console_errors)
    g.add_node("daily_qna", daily_qna)

    g.set_entry_point("classify")

    def route_by_type(s: State):
        """根据 `type` 字段选择分支：dev/bugfix/qna"""
        t = s.get("type","qna")
        if t == "dev":
            return "read_code"
        if t == "bugfix":
            return "read_logs"
        return "daily_qna"

    g.add_conditional_edges(
        "classify", route_by_type,
        {"read_code":"read_code","read_logs":"read_logs","daily_qna":"daily_qna"}
    )

    # 开发分支：读取代码 → 分析需求 → 设计 → 生成
    g.add_edge("read_code","analyze_requirements")
    g.add_edge("analyze_requirements","design_solution")
    g.add_edge("design_solution","generate_code")
    # 修复分支：读取日志 → 读取代码上下文 → 定位问题 → 生成修复
    g.add_edge("read_logs","read_code")
    g.add_edge("read_code","locate_issue")
    g.add_edge("locate_issue","fix_code")

    def next_after_code(s: State):
        """统一落到保存文件节点"""
        return "save_files"

    g.add_conditional_edges("generate_code", next_after_code, {"save_files":"save_files"})
    g.add_conditional_edges("fix_code", next_after_code, {"save_files":"save_files"})

    # 版本与服务
    g.add_edge("save_files","version_commit")
    g.add_edge("version_commit","service_manage")

    def after_service(s: State):
        """服务节点后进入控制台错误检查"""
        return "check_console_errors"

    g.add_conditional_edges("service_manage", after_service, {"check_console_errors":"check_console_errors"})

    def end_or_fix(s: State):
        """无错误则结束，有错误回到日志分析形成闭环"""
        if s.get("console_errors"):
            return "read_logs"
        return END

    g.add_conditional_edges("check_console_errors", end_or_fix, {"read_logs":"read_logs", END: END})
    g.add_edge("daily_qna", END)
    return g.compile()