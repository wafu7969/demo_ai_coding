import argparse, json
import os
from app.graph import build_app
from app.nodes import State

def run():
    # 命令行入口：解析参数并执行应用
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)  # 项目目录路径
    p.add_argument("--service", required=False)  # 可选：服务入口，如 'app.main:app'
    p.add_argument("--request", required=True)   # 用户请求/任务描述
    args = p.parse_args()  # 解析传入参数
    app = build_app()  # 构建 LangGraph 应用
    init: State = {"repo_path": args.repo, "service_entry": args.service, "request": args.request}  # 初始状态，传入图
    out = app.invoke(init)  # 执行图，返回最终状态字典
    print(json.dumps(out, ensure_ascii=False, indent=2))  # 以 JSON 格式美化输出

if __name__ == "__main__":  # 脚本直接执行时入口
    run()  # 运行入口函数