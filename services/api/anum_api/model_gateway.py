from pydantic import BaseModel


class ModelUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    provider: str
    model: str


class ModelResponse(BaseModel):
    text: str
    usage: ModelUsage


class MockModelGateway:
    provider = "mock"
    model = "anum-mock-planner"

    async def generate_text(self, prompt: str) -> ModelResponse:
        words = prompt.split()
        summary = " ".join(words[:18]) if words else "empty task"
        return ModelResponse(
            text=f"Prepared ANUM plan for: {summary}",
            usage=ModelUsage(
                input_tokens=max(1, len(words)),
                output_tokens=12,
                provider=self.provider,
                model=self.model,
            ),
        )
