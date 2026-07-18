import asyncio
import os
import json
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
# Googleの新しい公式SDKをインポート
from google import genai
from google.genai import types

# 環境変数の取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # 変更
GITHUB_TOKEN = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
REPO_OWNER = os.environ.get("REPOSITORY_OWNER")
REPO_NAME = os.environ.get("REPOSITORY_NAME")
PR_NUMBER = os.environ.get("PR_NUMBER")

async def main():
    if not all([GEMINI_API_KEY, GITHUB_TOKEN, REPO_OWNER, REPO_NAME, PR_NUMBER]):
        print("必要な環境変数が不足しています。")
        return

    # 1. GitHub MCP サーバーの起動パラメータ設定
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": GITHUB_TOKEN}
    )

    # Gemini クライアントの初期化
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    # 2. MCPサーバーを起動して接続
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # MCPサーバーからツール一覧を取得
            mcp_tools = await session.list_tools()
            
            # 💡 ここがポイント: 
            # MCPのTool形式をGeminiのFunction Declaration形式にパースして渡すか、
            # あるいはGemini用オブジェクトにラップして渡します。
            # ※簡易的にMCPツールをGeminiのtools形式として渡す実装例：
            gemini_tools = []
            for tool in mcp_tools.tools:
                gemini_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=tool.name,
                                description=tool.description,
                                parameters=tool.inputSchema
                            )
                        ]
                    )
                )

            # チャットセッションを開始（Geminiはチャット履歴の管理が自動なので楽です）
            system_instruction = (
                "あなたは優秀なシニアエンジニアです。与えられたGitHub MCPツールを使って対象のPR情報を取得し、"
                "コードレビューを行ってください。重大な問題がある場合は、該当するファイルと行数に対して"
                "create_pull_request_comment ツールを使って具体的にレビューコメントを残してください。"
            )
            
            # モデルは Gemini 2.5 Pro などを指定（複雑なコード追跡が得意です）
            chat = gemini_client.chats.create(
                model="gemini-2.5-pro", 
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=gemini_tools
                )
            )

            user_message = f"リポジトリ「{REPO_OWNER}/{REPO_NAME}」の PR #{PR_NUMBER} をレビューしてください。"
            print("🤖 GeminiによるAIレビューを開始します...")
            
            # 最初のメッセージ送信
            response = chat.send_message(user_message)

            # 3. ツール呼び出しの無限ループ処理
            while True:
                # Geminiがツール呼び出しを要求しているかチェック
                if not response.function_calls:
                    # ツール呼び出しがなければ終了
                    print("\n🎉 レビューが正常に完了しました。")
                    print(response.text)
                    break

                tool_responses = []
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    # Geminiは引数を辞書形式で持っています
                    tool_args = function_call.args 
                    
                    print(f"🛠️ Gemini Tool Call: {tool_name} (Args: {json.dumps(tool_args)})")

                    try:
                        # MCPサーバー経由でツールを実行
                        mcp_result = await session.call_tool(tool_name, arguments=tool_args)
                        output_text = "".join([content.text for content in mcp_result.content if content.type == "text"])
                        
                        # Geminiへ返す結果オブジェクトを作成
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"result": output_text}
                            )
                        )
                        print(f"✅ Tool {tool_name} の実行に成功しました。")
                    except Exception as e:
                        print(f"❌ Tool {tool_name} でエラーが発生しました: {e}")
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=tool_name,
                                response={"error": str(e)}
                            )
                        )

                # ツールの実行結果をGeminiにフィードバックして次のステップを仰ぐ
                response = chat.send_message(tool_responses)

if __name__ == "__main__":
    asyncio.run(main())