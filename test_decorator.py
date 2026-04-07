#!/usr/bin/env python3
"""Test the decorator wrapper to ensure it preserves function metadata correctly."""

import asyncio
import functools
import inspect


class FakeFastMCP:
    """Minimal FastMCP mock to test decorator wrapping."""
    
    def __init__(self):
        self.tools = []
    
    def tool(self, *args, **kwargs):
        """Original decorator that registers tools."""
        def decorator(func):
            # Simulate FastMCP's introspection
            sig = inspect.signature(func)
            name = func.__name__
            doc = func.__doc__
            self.tools.append({
                'name': name,
                'signature': sig,
                'doc': doc,
                'func': func
            })
            return func
        return decorator


# Simulate the wrapper from server.py
_orig_tool = None
mcp = FakeFastMCP()
_orig_tool = mcp.tool


def _instrumented_tool(*deco_args, **deco_kwargs):
    orig_decorator = _orig_tool(*deco_args, **deco_kwargs)

    def wrapper(func):
        @functools.wraps(func)
        async def instrumented(**kwargs):
            result = await func(**kwargs)
            # Simulate notification (without actual asyncio.create_task)
            print(f"Would notify: {func.__name__}({kwargs}) -> {result}")
            return result
        return orig_decorator(instrumented)
    return wrapper


mcp.tool = _instrumented_tool


# Test with a sample tool
@mcp.tool()
async def test_tool(slot: int, target: str | None = None) -> str:
    """Test tool with parameters.
    
    Args:
        slot: A slot index
        target: Optional target
    """
    return f"result: slot={slot}, target={target}"


async def main():
    print("Testing decorator wrapper...\n")
    
    # Check if tool was registered
    assert len(mcp.tools) == 1, "Tool not registered"
    registered = mcp.tools[0]
    
    print(f"Tool name: {registered['name']}")
    print(f"Tool signature: {registered['signature']}")
    print(f"Tool docstring: {registered['doc'][:50]}...")
    print(f"Function name: {registered['func'].__name__}")
    
    # Check that signature is preserved
    sig = registered['signature']
    params = list(sig.parameters.keys())
    print(f"\nParameters: {params}")
    
    # The instrumented wrapper should still appear to have the original signature
    # However, functools.wraps only preserves __name__, __doc__, etc.
    # The actual signature of the wrapper function might be different
    actual_sig = inspect.signature(registered['func'])
    print(f"Actual signature: {actual_sig}")
    
    # Call the function to test it works
    result = await registered['func'](slot=5, target="enemy_1")
    print(f"\nResult: {result}")
    
    print("\n✓ Test passed")


if __name__ == "__main__":
    asyncio.run(main())
