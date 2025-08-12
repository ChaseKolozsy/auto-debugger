"""
Function block parsing and exploration utilities.

This module provides utilities to split functions into logical blocks
separated by blank lines for easier navigation during audio debugging.
"""

from __future__ import annotations

from typing import List, Tuple, Optional, Dict, Any


def parse_function_blocks(function_body: str) -> List[str]:
    """
    Parse a function body into blocks separated by blank lines.
    
    A block is a continuous section of code without blank lines.
    
    Args:
        function_body: The function body text
        
    Returns:
        List of code blocks (non-empty sections)
    """
    if not function_body:
        return []
    
    lines = function_body.split('\n')
    blocks = []
    current_block = []
    
    for line in lines:
        # Check if line is blank (only whitespace)
        if line.strip():
            # Non-blank line, add to current block
            current_block.append(line)
        else:
            # Blank line - end current block if it has content
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
    
    # Don't forget the last block if it exists
    if current_block:
        blocks.append('\n'.join(current_block))
    
    return blocks


def get_block_preview(block: str, max_length: int = 50) -> str:
    """
    Get a brief preview of a code block for announcement.
    
    Args:
        block: The code block
        max_length: Maximum length of preview
        
    Returns:
        Brief preview of the block's first line
    """
    lines = block.split('\n')
    first_line = lines[0].strip() if lines else ""
    
    if len(first_line) > max_length:
        return first_line[:max_length - 3] + "..."
    return first_line


class FunctionBlockExplorer:
    """
    Interactive explorer for function blocks.
    
    Allows paginated navigation through function blocks.
    """
    
    def __init__(self, function_body: str, tts: Any = None):
        """
        Initialize the block explorer.
        
        Args:
            function_body: The function body to explore
            tts: Text-to-speech instance
        """
        self.blocks = parse_function_blocks(function_body)
        self.tts = tts
        self.current_page = 0
        self.blocks_per_page = 10
        self.total_pages = (len(self.blocks) + self.blocks_per_page - 1) // self.blocks_per_page
        
    def get_current_page_blocks(self) -> List[Tuple[int, str]]:
        """
        Get blocks for the current page.
        
        Returns:
            List of (index, block) tuples for current page
        """
        start_idx = self.current_page * self.blocks_per_page
        end_idx = min(start_idx + self.blocks_per_page, len(self.blocks))
        
        page_blocks = []
        for i in range(start_idx, end_idx):
            page_blocks.append((i - start_idx, self.blocks[i]))
        
        return page_blocks
    
    def announce_page_info(self) -> str:
        """
        Get announcement text for current page info.
        
        Returns:
            Text to announce about current page
        """
        if not self.blocks:
            return "No blocks found in function"
        
        total_blocks = len(self.blocks)
        if self.total_pages == 1:
            return f"Function has {total_blocks} blocks. Choose 0 to {total_blocks - 1}"
        else:
            start_idx = self.current_page * self.blocks_per_page
            end_idx = min(start_idx + self.blocks_per_page - 1, total_blocks - 1)
            actual_end = min(9, end_idx - start_idx)
            return f"Page {self.current_page + 1} of {self.total_pages}. Choose 0 to {actual_end}, N for next page, P for previous page"
    
    def select_block(self, index: int) -> Optional[str]:
        """
        Select a block by index on current page.
        
        Args:
            index: Block index on current page (0-9)
            
        Returns:
            The selected block text or None if invalid
        """
        page_blocks = self.get_current_page_blocks()
        for page_idx, block in page_blocks:
            if page_idx == index:
                return block
        return None
    
    def next_page(self) -> bool:
        """
        Move to next page.
        
        Returns:
            True if moved to next page, False if already at last page
        """
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            return True
        return False
    
    def previous_page(self) -> bool:
        """
        Move to previous page.
        
        Returns:
            True if moved to previous page, False if already at first page
        """
        if self.current_page > 0:
            self.current_page -= 1
            return True
        return False
    
    def speak_block(self, block: str, is_code: bool = True) -> None:
        """
        Speak a code block using TTS.
        
        Args:
            block: The block to speak
            is_code: Whether to treat as code for syntax conversion
        """
        if self.tts:
            self.tts.speak(block, interrupt=True, is_code=is_code)
            import time
            while self.tts.is_speaking():
                time.sleep(0.05)