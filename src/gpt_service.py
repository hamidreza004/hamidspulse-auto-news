import json
import os
from typing import Dict, Any, Optional
from openai import OpenAI
from src.config import Config


class GPTService:
    def __init__(self, config: Config):
        self.config = config
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=base_url
        )
    
    def triage_message(self, message_text: str, source_channel: str, 
                      source_url: str, current_state: str) -> Optional[Dict[str, Any]]:
        core_characteristics = "\n".join([f"- {char}" for char in self.config.get('content_style.core_characteristics', [])])
        
        system_prompt = f"""شما سیستم تریاژ خبری برای کانال "Hamid's Pulse" هستید.

ویژگی‌های کانال:
{core_characteristics}

وظیفه: تحلیل پیام پایین (نه توضیحاتی که بالا راجع به دانسته‌هامون دادم) و تعیین دسته اهمیت این پیام پایین بر اساس ویژگی‌های کانال.

دسته‌بندی:
- HIGH: خبر فوری که باید فوراً منتشر شود و یا خیلی زیاد این پیام میتواند تاثیرگذار باشد در آینده نزدیک
- MEDIUM: خبر مهم که برای خلاصه ساعتی مفید است
- LOW:
 خبر کم‌اهمیت که رد می‌شود و در خلاصه های روزانه می‌آید، خبرهای نصفه یا تک جمله هایی که ارزش خبری ندارند و صاحب کانال صرفا حرف خودش را زده یا تبلیغ گذاشته در این دسته هستند.

خروجی: فقط JSON (بدون markdown، بدون توضیح اضافه):
{{
  "bucket": "high" | "medium" | "low",
  "novelty_delta": "یک جمله فارسی: چه چیزی در پیام مربوطه پایین به نسبت دانسته ما جدید است؟ اگر پیام بی ارزش هست هم بگو",
  "reason": "دلیل کوتاه فارسی",
  "key_points": ["نکته 1", "نکته 2"]
}}"""

        user_prompt = f"""وضعیت فعلی خبری (Situation Brief):
{current_state}

---

کانال منبع این پیام: {source_channel}
لینک این پیام: {source_url}

این اون پیامی هست که قراره تو قضاوت کنی، پیام پایین تنها چیزیه که قراره نظر خودت رو در موردش بگی، اگه خیلی کوتاهه یا نامربوطه باید همین رو ذکر کنی.
پیام مربوطه:

{message_text}

میخوای یه بار دیگه تکرار میکنم پیام رو 

---

{message_text}

---

این پیام بالا را فقط تریاژ کن و JSON خروجی بده."""

        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Calling GPT triage with model: {self.config.triage_model}")
            
            response = self.client.chat.completions.create(
                model=self.config.triage_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=1.0,
                max_tokens=self.config.get('gpt_models.max_tokens_triage', 50000)
            )
            
            logger.info(f"GPT response received. Choices count: {len(response.choices)}")
            if response.choices:
                logger.info(f"First choice finish_reason: {response.choices[0].finish_reason}")
            
            content = response.choices[0].message.content
            logger.info(f"Response content length: {len(content) if content else 0}")
            logger.info(f"Response content preview: '{content[:100] if content else 'NONE'}...'")
            if not content or content.strip() == "":
                logger.error(f"GPT returned empty response. Model: {self.config.triage_model}")
                logger.error(f"Response object: {response}")
                logger.error(f"Full API response for debugging: {response.model_dump_json() if hasattr(response, 'model_dump_json') else str(response)}")
                return None
            
            # Extract JSON from markdown code blocks if present
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                content = content.replace("```json", "").replace("```", "").strip()
            
            try:
                result = json.loads(content)
            except json.JSONDecodeError as je:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"JSON decode error. Response content: '{content[:500]}...'")
                logger.error(f"Full response for debugging: {content}")
                raise
            
            bucket = result.get('bucket', 'low')
            logger.info(f"Triage: bucket={bucket}, reason={result.get('reason', '')[:50]}")
            
            return result
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in GPT triage: {e}", exc_info=True)
            return None
    
    def generate_high_post(self, message_text: str, source_channel: str,
                          source_url: str, triage_result: dict, current_state: str) -> Optional[str]:
        core_characteristics = "\n".join([f"- {char}" for char in self.config.get('content_style.core_characteristics', [])])
        emoji_rules = self.config.get('content_style.emoji_logic', {})
        high_emoji_count = emoji_rules.get('high_news_emoji_count', 3)
        emoji_guidelines = emoji_rules.get('guidelines', '')
        
        system_prompt = f"""شما نویسنده محتوای کانال "Hamid's Pulse" هستید.

ویژگی‌های کانال:
{core_characteristics}

قالب دقیق پست HIGH:
{high_emoji_count} ایموجی **عنوان خبر (bold)**

[{source_channel} | لینک]({source_url})

متن توضیح در یک یا دو جمله

@hamidspulse 🔭

مهم: حتماً URL واقعی را در پرانتز بگذار، نه کلمه "URL" یا "لینک"

قوانین ایموجی:
{emoji_guidelines}

نکات:
- ایموجی‌ها قبل از عنوان در همان خط
- عنوان bold، 3-6 کلمه، بدون براکت
- لینک منبع بلافاصله در خط بعدی
- متن توضیح کوتاه و مفید بعد از لینک
- منبع با فرمت markdown دقیق: [{source_channel} | لینک]({source_url})
- URL باید دقیقاً همان لینکی باشد که در بخش "لینک" داده شده
- فقط یک بار @hamidspulse 🔭 در انتها"""

        key_points = "\n".join([f"- {p}" for p in triage_result.get('key_points', [])])
        user_prompt = f"""وضعیت فعلی:
{current_state[:500]}

---

خبر جدید HIGH:
منبع: {source_channel}
لینک: {source_url}

متن:
{message_text}

نکات کلیدی:
{key_points}

دلیل: {triage_result.get('reason', '')}
نوآوری: {triage_result.get('novelty_delta', '')}

یک پست جذاب بنویس (قالب دقیق بالا)."""

        try:
            response = self.client.chat.completions.create(
                model=self.config.content_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=1.0,
                max_tokens=self.config.get('gpt_models.max_tokens_content', 50000)
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"Error generating HIGH post: {e}")
            return None
    
    def generate_hourly_digest(self, medium_items: list, current_state: str,
                              start_time: str, end_time: str) -> Optional[str]:
        core_characteristics = "\n".join([f"- {char}" for char in self.config.get('content_style.core_characteristics', [])])
        min_bullets = self.config.get('content_style.writing_guidelines.min_bullets_per_digest', 3)
        max_bullets = self.config.get('content_style.writing_guidelines.max_bullets_per_digest', 8)
        
        # Format time as hours only with bold (e.g., "**23:00-00:00**")
        start_hour = start_time.strftime('%H:%M') if hasattr(start_time, 'strftime') else str(start_time)
        end_hour = end_time.strftime('%H:%M') if hasattr(end_time, 'strftime') else str(end_time)
        title = f"🕐 برخی اخبار **{start_hour}–{end_hour}**"
        
        system_prompt = f"""شما نویسنده محتوای کانال "Hamid's Pulse" هستید.

ویژگی‌های کانال:
{core_characteristics}

قالب دقیق:
{title}

[هر خبر با ایموجی]
[نام منبع | لینک](URL دقیق از لیست زیر)

[خبر بعدی با ایموجی]
[نام منبع | لینک](URL دقیق از لیست زیر)

... ({min_bullets}-{max_bullets} خبر)

@hamidspulse 🔭

نکات مهم:
- هر خبر = یک جمله کوتاه با ایموجی
- بلافاصله زیر هر خبر، منبع آن در خط جداگانه
- منابع با فرمت markdown: [نام منبع | لینک](URL)
- حتماً URL دقیق از لیست اخبار زیر استفاده کن - هر خبر URL خودش را دارد
- اگر از چند منبع استفاده می‌کنی، همه URLها را درست بگذار
- فقط خبر خام، نه تحلیل یا حدس
- بدون عبارات مثل "این یعنی"، "احتمال"، "ممکن است"
- {min_bullets}-{max_bullets} خبر کلاً"""

        items_text = ""
        for idx, item in enumerate(medium_items, 1):
            items_text += f"\n{idx}. منبع: {item['source_channel']}\n"
            items_text += f"   لینک: {item['source_url']}\n"
            items_text += f"   متن: {item['message_text'][:300]}...\n"
            items_text += f"   نکات کلیدی: {', '.join(item['triage_json'].get('key_points', []))}\n"
        
        user_prompt = f"""وضعیت فعلی:
{current_state}

---

اخبار با اهمیت متوسط در ساعت گذشته ({len(medium_items)} مورد):
{items_text}

---

یک خلاصه ساعتی بنویس - هر bullet فقط یک خبر، بدون تحلیل یا حدس."""

        try:
            response = self.client.chat.completions.create(
                model=self.config.content_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=1.0,
                max_tokens=self.config.get('gpt_models.max_tokens_content', 50000)
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"Error generating 3-hour digest: {e}")
            return None
    
    def update_situation_brief(self, current_brief: str, new_event: str, 
                              event_type: str = "high_post") -> str:
        system_prompt = """شما مدیر حافظه خبری هستید. وظیفه‌تان به‌روزرسانی "Situation Brief" است.

Situation Brief = خلاصه فشرده وضعیت فعلی خبری (حداکثر 1200 کاراکتر)

وظیفه: 
1. Brief فعلی را بخوان
2. رویداد جدید را بگیر
3. Brief جدید بساز که:
   - اطلاعات قدیمی کم‌اهمیت را حذف کند
   - رویداد جدید را اضافه کند
   - فشرده و مفید باشد
   - زمینه کافی برای تریاژ بعدی بدهد

فقط متن Brief جدید را برگردان، بدون توضیح اضافی."""

        user_prompt = f"""Brief فعلی:
{current_brief}

---

رویداد جدید ({event_type}):
{new_event}

---

Brief جدید (حداکثر 1200 کاراکتر):"""

        try:
            response = self.client.chat.completions.create(
                model=self.config.triage_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=1.0,
                max_tokens=50000
            )
            
            new_brief = response.choices[0].message.content.strip()
            return new_brief[:1200]
            
        except Exception as e:
            print(f"Error updating situation brief: {e}")
            return current_brief
