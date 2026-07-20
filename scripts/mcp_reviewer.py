import asyncio
import os
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai
from google.genai import types

# 環境変数の取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
REPO_OWNER = os.environ.get("REPOSITORY_OWNER")
REPO_NAME = os.environ.get("REPOSITORY_NAME")
PR_NUMBER_STR = os.environ.get("PR_NUMBER", "0")
PR_NUMBER = int(PR_NUMBER_STR) if PR_NUMBER_STR.isdigit() else 0


def sanitize_schema(schema: dict) -> dict:
    """
    Gemini APIがサポートしていないJSON Schemaのメタキー（$ref, $defs, $schema, oneOf等）を
    再帰的に除去・クリーンアップする関数
    """
    if not isinstance(schema, dict):
        return schema

    cleaned = {}
    # Gemini APIが400 INVALID_ARGUMENTエラーを返す原因となるキーを排除
    forbidden_keys = {
        "$schema", "$id", "$ref", "$defs", "definitions", 
        "oneOf", "anyOf", "allOf"
    }

    for key, value in schema.items():
        if key in forbidden_keys:
            continue
        
        if isinstance(value, dict):
            cleaned[key] = sanitize_schema(value)
        elif isinstance(value, list):
            cleaned_list = []
            for item in value:
                if isinstance(item, dict):
                    cleaned_list.append(sanitize_schema(item))
                else:
                    cleaned_list.append(item)
            cleaned[key] = cleaned_list
        else:
            cleaned[key] = value

    # propertiesが存在するがtypeが未定義の場合はOBJECTを自動設定（Gemini仕様対策）
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "OBJECT"

    return cleaned


async def main():
    if not all([GEMINI_API_KEY, GITHUB_TOKEN, REPO_OWNER, REPO_NAME, PR_NUMBER]):
        print("❌ 必要な環境変数が不足しています。")
        return

    # 1. GitHub MCP サーバーの起動パラメータ（npx経由でstdio起動）
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": GITHUB_TOKEN}
    )

    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    print("🚀 GitHub MCP サーバーに接続中...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("✅ MCP サーバーとのセッションが確立されました。")

            # MCPのツール一覧を取得し、Geminiが解釈できる形式に変換
            mcp_tools = await session.list_tools()
            gemini_tools = []

            for tool in mcp_tools.tools:
                raw_schema = dict(tool.inputSchema) if tool.inputSchema else {}
                cleaned_schema = sanitize_schema(raw_schema)

                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool.name,
                                description=tool.description,
                                parameters=cleaned_schema
                            )
                        ]
                    )
                )

            # Geminiへの指示プロンプト
            system_instruction = f"""
あなたは優秀なシニアエンジニアです。
提供された GitHub MCP ツールを自律的に使用し、対象のプルリクエスト（PR）のコードレビューを行ってください。

【レビューの手順】
1. まず `get_pull_request` でPRの目的や概要を確認します。
2. `get_pull_request_files` で変更されたファイルと差分（diff）を確認します。
3. 必要に応じて `get_file_contents` や `view_file_item` を使い、変更箇所周辺のコード文脈を把握してください。
4. バグの可能性、可読性、セキュリティ、設計の観点からチェックを行います。

【コメントルール】
- 改善すべき明確な問題点がある場合は、`create_pull_request_comment` ツールを使って該当するファイルの正確なパス（path）と行数（line）にインラインコメントを残してください。
- レビューが全て完了したら、最終的な全体要約を出力して終了してください。

【現在のコンテキスト】
Owner: {REPO_OWNER}
Repository: {REPO_NAME}
PR Number: {PR_NUMBER}
"""

            # チャットセッションを作成
            chat = gemini_client.chats.create(
                model="gemini-2.5-pro",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=gemini_tools,
                    temperature=0.2
                )
            )

            initial_prompt = f"PR #{PR_NUMBER} の自動レビューを開始してください。"
            print("🤖 Gemini による自律レビューを開始します...")
            
            response = chat.send_message(initial_prompt)

            # 2. Tool Call（関数呼び出し）のループ処理
            loop_count = 0
            max_loops = 15  # 無限ループ防止用のセーフティ

            while loop_count < max_loops:
                loop_count += 1
                
                # Geminiがツール呼び出しを必要としない場合はレビュー完了
                if not response.function_calls:
                    print("\n🎉 レビューが正常に完了しました。")
                    print("--- 全体要約 ---")
                    print(response.text)
                    break

                tool_responses = []
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    tool_args = function_call.args if function_call.args else {}
                    
                    # LLMが共通引数を省略した場合の自動補完（エラー防止）
                    if "owner" not in tool_args:
                        tool_args["owner"] = REPO_OWNER
                    if "repo" not in tool_args:
                        tool_args["repo"] = REPO_NAME
                    if "pull_number" not in tool_args and "number" not in tool_args:
                        if tool_name in ["get_pull_request", "get_pull_request_files", "create_pull_request_comment"]:
                            tool_args["pull_number"] = PR_NUMBER

                    print(f" 🛠️ [Loop {loop_count}] Tool Executing: {tool_name}")

                    try:
                        # MCPサーバー経由でGitHub APIを実行
                        mcp_result = await session.call_tool(tool_name, arguments=tool_args)
                        output_text = "".join([content.text for content in mcp_result.content if content.type == "text"])
                        
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"status": "success", "result": output_text}
                            )
                        )
                        print(f"   ✅ Success: {tool_name}")
                    except Exception as e:
                        print(f"   ❌ Error in {tool_name}: {e}")
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"status": "error", "error": str(e)}
                            )
                        )

                # ツールの実行結果をGeminiにフィードバックして次のステップを判定
                response = chat.send_message(tool_responses)
            else:
                print("\n⚠️ 思考ループの最大回数に達したため終了しました。")

if __name__ == "__main__":
    asyncio.run(main())