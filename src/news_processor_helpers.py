import logging
import json

logger = logging.getLogger(__name__)


async def find_similar_post_with_gpt(gpt_service, message_data: dict, triage_result: dict, recent_posts: list, current_state: str = "") -> dict:
    """
    Use GPT-5-2-low to check if new message is related to any of the last 5 recent posts
    Returns the related post if found, None otherwise
    """
    if not recent_posts:
        logger.info("No recent posts to compare")
        return None
    
    # Limit to last 5 posts
    recent_posts = recent_posts[:5]
    
    # Get core characteristics
    core_characteristics = "\n".join([f"- {char}" for char in gpt_service.config.get('content_style.core_characteristics', [])])
    
    # Prepare recent posts list (keep it short to reduce prompt tokens)
    posts_text = ""
    for idx, post in enumerate(recent_posts, 1):
        content = post.get('content', '')[:200]
        posts_text += f"{idx}. {content}\n\n"
    
    system_prompt = f"""You are a news similarity analyzer for "Hamid's Pulse" channel.

Current situation:
{current_state}

Task: Determine if a new message is about the SAME topic/event as any recent post, feel free to leave 0 that means the category is whole different.

Output ONLY JSON (no markdown, no explanation):
{{
  "related_post_number": 0,
  "reason": "short reason in Persian"
}}

- If new message is about SAME topic as post 1-5, return that number
- If it's a DIFFERENT topic, return 0"""
    
    user_prompt = f"""Recent published posts:
{posts_text}

---

New message: (this below message is exactly what you should analyze with to posts above)
{message_data['message_text']}

another time, the message is:
{message_data['message_text']}

Key points: {', '.join(triage_result.get('key_points', []))}
Novelty: {triage_result.get('novelty_delta', '')}

---

Which post (1-5) is this about the SAME topic? Or 0 if different topic?"""
    
    try:
        logger.info(f"Calling GPT for similarity check with {len(recent_posts)} posts")
        response = gpt_service.client.chat.completions.create(
            model="openai/gpt-5-nano",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=1.0,
            max_tokens=50000  # Increased: GPT-5-nano uses ~300 for reasoning, need more for actual response
        )
        
        logger.info(f"GPT similarity call completed, response received")
        result_text = response.choices[0].message.content.strip() if response.choices else ""
        
        if not result_text:
            logger.warning("⚠️ GPT returned empty response for similarity check")
            logger.warning(f"Response object: {response}")
            return None
        
        logger.info(f"GPT similarity response: {result_text[:200]}")
        
        # Clean markdown if present
        if result_text.startswith('```'):
            parts = result_text.split('```')
            if len(parts) >= 2:
                result_text = parts[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
                result_text = result_text.strip()
        
        result = json.loads(result_text)
        post_number = result.get('related_post_number', 0)
        reason = result.get('reason', '')
        
        if post_number > 0 and post_number <= len(recent_posts):
            logger.info(f"✓ GPT found similar post #{post_number}: {reason}")
            return recent_posts[post_number - 1]
        
        logger.info(f"✓ GPT: No similar post (returned {post_number})")
        return None
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in similarity check: {e}")
        logger.error(f"Response was: {result_text[:500] if 'result_text' in locals() else 'NONE'}")
        return None
    except Exception as e:
        logger.error(f"Error in GPT similarity check: {e}", exc_info=True)
        return None
