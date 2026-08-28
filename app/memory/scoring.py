from .models import Memory

def score_memory(memory: Memory):
    score = memory.importance
    if memory.confidence == "High": score *= 1.5
    elif memory.confidence == "Low": score *= 0.5
    return score
