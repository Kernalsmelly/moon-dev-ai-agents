"""
I built this million agent bc it has a million context window. The current problem with AI is the context window. 
Gemini just destroyed that problem and released Gemini Flash 2.0 with a million context window. 
This agent is a simple LLM where you can ask back and forth questions based off your knowledge-base 
Set up a knowledge base in the data folder called million_agent. 
"""

from pathlib import Path
import glob
from termcolor import cprint
from src.models import model_factory

# Constants
KNOWLEDGE_BASE_FOLDER = Path(__file__).parent.parent / "data" / "million_agent"

class BookAgent:
    """Agent for processing and analyzing text documents using Gemini 2.0"""
    
    def __init__(self):
        """Initialize the book agent with Gemini 2.0 Flash model"""
        cprint("🌙 Moon Dev's Book Agent Initializing...", "cyan")
        self.model = model_factory.get_model("gemini", "gemini-2.0-flash")
        cprint(f"📚 Loading knowledge base from: {KNOWLEDGE_BASE_FOLDER}", "cyan")
        self.knowledge_base = self._load_knowledge_base()
        
    def _load_knowledge_base(self) -> str:
        """Combine all text files in the knowledge base folder"""
        combined_text = []
        files = glob.glob(str(KNOWLEDGE_BASE_FOLDER / "*.txt"))
        
        if not files:
            cprint("❌ No text files found in knowledge base!", "red")
            return ""
            
        cprint(f"📖 Found {len(files)} files in knowledge base:", "green")
        for file_path in files:
            filename = Path(file_path).name
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    combined_text.append(f"\n=== {filename} ===\n{content}")
                cprint(f"  ├─ Loaded: {filename}", "green")
            except Exception as e:
                cprint(f"❌ Error reading {filename}: {str(e)}", "red")
                
        cprint("  └─ Knowledge base loaded! 🚀", "green")
        return "\n".join(combined_text)
    
    def ask(self, question: str) -> str:
        """Ask a question about the knowledge base"""
        if not self.knowledge_base:
            return "❌ Error: Knowledge base is empty!"
            
        cprint(f"🤔 Processing question: {question}", "cyan")
        
        prompt = f"""
        === KNOWLEDGE BASE CONTENT ===
        {self.knowledge_base}
        
        === QUESTION ===
        {question}
        
        Please provide a detailed answer based on the knowledge base content above.
        Include relevant quotes when possible to support your response.
        """
        
        try:
            response = self.model.generate_response(
                system_prompt="You are Moon Dev's Knowledge Base AI 🌙. Answer questions based on the provided content only.",
                user_content=prompt,
                temperature=0.7
            )
            cprint("✨ Response generated!", "green")
            return response.content
            
        except Exception as e:
            error_msg = f"❌ Error generating response: {str(e)}"
            cprint(error_msg, "red")
            return error_msg

def main():
    """CLI interface for the Book Agent"""
    agent = BookAgent()
    
    cprint("\n🌙 Moon Dev's Knowledge Base Assistant", "cyan")
    cprint("Type 'exit' to quit", "yellow")
    
    while True:
        try:
            question = input("\n❓ Ask a question: ")
            if question.lower() in ['exit', 'quit']:
                break
                
            if question.strip():
                print("\n🤖 Answer:")
                print(agent.ask(question))
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            cprint(f"❌ Error: {str(e)}", "red")
    
    cprint("\n👋 Thanks for using Moon Dev's Knowledge Base Assistant! 🌙", "cyan")

if __name__ == "__main__":
    main() 