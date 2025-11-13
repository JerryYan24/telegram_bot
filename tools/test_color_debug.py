#!/usr/bin/env python3
"""
独立测试脚本：调试日历事件颜色问题
测试GPT返回的颜色值、类别映射和最终颜色ID
"""

import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 添加项目路径
sys.path.insert(0, '/home/jerry/Documents/telegram_bot')

from smart_assistant.openai_parser import OpenAIEventParser
from smart_assistant.assistant import CalendarAutomationAssistant, DEFAULT_CATEGORY_COLORS
from smart_assistant.colors import normalize_color_hint
from smart_assistant.models import CalendarEvent


def load_config():
    """加载配置文件"""
    import yaml
    with open('/home/jerry/Documents/telegram_bot/config.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def test_color_normalization():
    """测试颜色标准化函数"""
    print("=" * 60)
    print("测试1: 颜色标准化函数")
    print("=" * 60)
    
    test_cases = [
        ("blue", "9"),
        ("red", "11"),
        ("green", "10"),
        ("9", "9"),
        ("blueberry", "9"),
        ("蓝", "9"),
        ("蓝色", "9"),
        ("work", None),  # 类别名不应该被识别为颜色
        ("", None),
        (None, None),
    ]
    
    for input_val, expected in test_cases:
        result = normalize_color_hint(input_val)
        status = "✓" if result == expected else "✗"
        print(f"{status} normalize_color_hint({repr(input_val)}) = {repr(result)} (期望: {repr(expected)})")
    
    print()


def test_category_colors():
    """测试类别到颜色的映射"""
    print("=" * 60)
    print("测试2: 类别到颜色映射")
    print("=" * 60)
    
    config = load_config()
    category_colors = config.get('google', {}).get('category_colors', {})
    default_color_id = config.get('google', {}).get('default_color_id', '')
    
    print(f"配置文件中的 category_colors: {category_colors}")
    print(f"配置文件中的 default_color_id: {repr(default_color_id)}")
    print()
    print("默认类别颜色映射:")
    for cat, color in DEFAULT_CATEGORY_COLORS.items():
        print(f"  {cat}: {color}")
    print()
    
    # 测试类别映射
    test_categories = ["work", "meeting", "personal", "travel", "medical", "unknown"]
    for cat in test_categories:
        if cat in DEFAULT_CATEGORY_COLORS:
            print(f"  类别 '{cat}' -> 默认颜色: {DEFAULT_CATEGORY_COLORS[cat]}")
        if category_colors and cat in category_colors:
            print(f"  类别 '{cat}' -> 配置颜色: {category_colors[cat]}")
    print()


def test_gpt_response_parsing():
    """测试GPT响应的解析"""
    print("=" * 60)
    print("测试3: GPT响应解析（模拟）")
    print("=" * 60)
    
    # 模拟几个GPT可能返回的JSON响应
    mock_responses = [
        {
            "has_entry": True,
            "entry_type": "event",
            "title": "工作会议",
            "start": "2024-12-20T10:00:00",
            "end": "2024-12-20T11:00:00",
            "timezone": "America/Los_Angeles",
            "category": "work",
            "color": None,  # 没有明确指定颜色
        },
        {
            "has_entry": True,
            "entry_type": "event",
            "title": "个人约会",
            "start": "2024-12-20T14:00:00",
            "end": "2024-12-20T15:00:00",
            "timezone": "America/Los_Angeles",
            "category": "personal",
            "color": "blue",  # GPT返回了"blue"
        },
        {
            "has_entry": True,
            "entry_type": "event",
            "title": "旅行计划",
            "start": "2024-12-21T09:00:00",
            "end": "2024-12-21T10:00:00",
            "timezone": "America/Los_Angeles",
            "category": "travel",
            "color": "9",  # GPT返回了数字"9"
        },
    ]
    
    config = load_config()
    parser = OpenAIEventParser(
        api_key=config['openai']['api_key'],
        default_timezone=config['assistant']['default_tz'],
        base_url=config['openai'].get('base_url'),
        text_model=config['openai']['text_model'],
    )
    
    for i, mock_payload in enumerate(mock_responses, 1):
        print(f"\n模拟响应 {i}:")
        print(json.dumps(mock_payload, indent=2, ensure_ascii=False))
        
        try:
            event = parser._dict_to_event(mock_payload)
            print(f"  解析后的事件:")
            print(f"    标题: {event.title}")
            print(f"    类别: {repr(event.category)}")
            print(f"    颜色ID: {repr(event.color_id)}")
            
            # 应用类别颜色
            category_colors = config.get('google', {}).get('category_colors')
            default_color_id = config.get('google', {}).get('default_color_id', '')
            
            from smart_assistant.assistant import CalendarAutomationAssistant
            # 创建临时assistant来测试颜色应用
            temp_assistant = CalendarAutomationAssistant(
                parser=parser,
                calendar_client=None,
                category_colors=category_colors,
                default_color_id=default_color_id if default_color_id else None,
            )
            temp_assistant._apply_category_color(event)
            print(f"    应用类别颜色后: {repr(event.color_id)}")
        except Exception as e:
            print(f"  解析错误: {e}")
    
    print()


def test_real_gpt_call():
    """测试真实的GPT调用"""
    print("=" * 60)
    print("测试4: 真实GPT调用")
    print("=" * 60)
    
    config = load_config()
    
    test_inputs = [
        "明天下午2点有个工作会议",
        "下周三上午10点个人约会，用红色标记",
        "12月25日旅行去纽约",
        "下周一上午9点看医生",
        "周五下午3点家庭聚餐",
        "下个月15号交房租",
        "明天下午4点学习Python",
    ]
    
    parser = OpenAIEventParser(
        api_key=config['openai']['api_key'],
        default_timezone=config['assistant']['default_tz'],
        base_url=config['openai'].get('base_url'),
        text_model=config['openai']['text_model'],
    )
    
    # 保存原始方法以便查看原始响应
    original_run_completion = parser._run_completion
    captured_responses = []
    
    def debug_run_completion(model, user_content):
        result = original_run_completion(model, user_content)
        # 保存GPT返回的原始payload
        if isinstance(result, dict):
            captured_responses.append({
                'color': result.get('color'),
                'color_id': result.get('color_id'),
                'colorId': result.get('colorId'),
                'category': result.get('category'),
            })
        return result
    
    # 临时替换方法
    parser._run_completion = debug_run_completion
    
    for i, test_input in enumerate(test_inputs):
        print(f"\n输入 {i+1}: {test_input}")
        try:
            parsed = parser.parse_text(test_input)
            
            # 显示捕获的响应
            if i < len(captured_responses):
                resp = captured_responses[i]
                print(f"  GPT返回的color字段: {repr(resp.get('color_field'))}")
                print(f"  GPT返回的category字段: {repr(resp.get('category'))}")
            print(f"  解析结果:")
            print(f"    事件数: {len(parsed.events)}")
            print(f"    任务数: {len(parsed.tasks)}")
            
            for event in parsed.events:
                print(f"\n  事件详情:")
                print(f"    标题: {event.title}")
                print(f"    类别: {repr(event.category)}")
                print(f"    颜色ID: {repr(event.color_id)}")
                
                # 应用类别颜色
                category_colors = config.get('google', {}).get('category_colors')
                default_color_id = config.get('google', {}).get('default_color_id', '')
                
                from smart_assistant.assistant import CalendarAutomationAssistant
                temp_assistant = CalendarAutomationAssistant(
                    parser=parser,
                    calendar_client=None,
                    category_colors=category_colors,
                    default_color_id=default_color_id if default_color_id else None,
                )
                
                color_before = event.color_id
                temp_assistant._apply_category_color(event)
                color_after = event.color_id
                
                print(f"    应用类别颜色前: {repr(color_before)}")
                print(f"    应用类别颜色后: {repr(color_after)}")
                
                # 检查是否是蓝色
                if color_after == "9":
                    print(f"    ⚠️  警告: 最终颜色是蓝色 (9)")
                    if color_before == "9":
                        print(f"    ⚠️  问题: GPT返回了蓝色，或者类别映射到了蓝色")
                    elif event.category == "travel":
                        print(f"    ℹ️  信息: 因为类别是'travel'，所以映射到蓝色(9)")
                    else:
                        print(f"    ⚠️  问题: 类别'{event.category}'不应该映射到蓝色")
                elif color_after:
                    print(f"    ✓ 最终颜色: {color_after}")
                else:
                    print(f"    ⚠️  警告: 没有设置颜色")
                    
        except Exception as e:
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()
    
    # 恢复原始方法
    parser._run_completion = original_run_completion
    
    # 总结
    print("\n" + "=" * 60)
    print("总结: GPT返回的color字段统计")
    print("=" * 60)
    blue_count = sum(1 for r in captured_responses if r.get('color_field') in ('blue', '9', 'blueberry'))
    total_count = len(captured_responses)
    if blue_count > 0:
        print(f"⚠️  发现 {blue_count}/{total_count} 个响应包含蓝色相关的color字段")
        print("   这可能是问题所在：GPT在用户没有明确指定颜色时也返回了'blue'")
    else:
        print(f"✓ 没有发现GPT自动返回蓝色")
    print()


def test_default_color_id_handling():
    """测试default_color_id的处理"""
    print("=" * 60)
    print("测试5: default_color_id处理")
    print("=" * 60)
    
    from smart_assistant.assistant import CalendarAutomationAssistant
    from smart_assistant.openai_parser import OpenAIEventParser
    from smart_assistant.models import CalendarEvent
    from datetime import datetime
    
    config = load_config()
    
    # 测试空字符串default_color_id
    test_cases = [
        ("", None, "空字符串应该被归一化为None"),
        (None, None, "None应该保持为None"),
        ("9", "9", "字符串'9'应该保持为'9'"),
        ("blue", "9", "'blue'应该被归一化为'9'"),
    ]
    
    parser = OpenAIEventParser(
        api_key=config['openai']['api_key'],
        default_timezone=config['assistant']['default_tz'],
    )
    
    for default_color_id, expected_normalized, description in test_cases:
        assistant = CalendarAutomationAssistant(
            parser=parser,
            calendar_client=None,
            category_colors={},  # 空映射，强制使用default
            default_color_id=default_color_id,
        )
        normalized = assistant.default_color_id
        status = "✓" if normalized == expected_normalized else "✗"
        print(f"{status} {description}")
        print(f"   输入: {repr(default_color_id)} -> 归一化后: {repr(normalized)} (期望: {repr(expected_normalized)})")
        
        # 测试应用到事件
        event = CalendarEvent(
            title="测试事件",
            start=datetime.now(),
            end=datetime.now(),
            timezone="UTC",
            category="unknown_category",  # 不在category_colors中
        )
        assistant._apply_category_color(event)
        print(f"   应用到事件后color_id: {repr(event.color_id)}")
        if default_color_id == "" and event.color_id == "9":
            print(f"   ⚠️  警告: 空字符串default_color_id被应用为'9'，这可能是bug!")
        print()
    
        print()


def test_category_normalization():
    """测试类别归一化功能"""
    print("=" * 60)
    print("测试6: 类别归一化功能")
    print("=" * 60)
    
    config = load_config()
    allowed_categories = list(config.get('google', {}).get('category_colors', {}).keys())
    
    parser = OpenAIEventParser(
        api_key=config['openai']['api_key'],
        default_timezone=config['assistant']['default_tz'],
        base_url=config['openai'].get('base_url'),
        text_model=config['openai']['text_model'],
        allowed_event_categories=allowed_categories,
    )
    
    print(f"允许的类别: {allowed_categories}")
    print()
    
    test_cases = [
        ("work", "work", "精确匹配"),
        ("health", "medical", "映射: health -> medical"),
        ("family", "personal", "映射: family -> personal"),
        ("study", "work", "映射: study -> work"),
        ("trip", "travel", "映射: trip -> travel"),
        ("unknown_category", allowed_categories[0] if allowed_categories else "unknown_category", "未知类别使用fallback"),
    ]
    
    for input_category, expected_category, description in test_cases:
        normalized = parser._normalize_category(input_category)
        status = "✓" if normalized == expected_category else "✗"
        print(f"{status} {description}")
        print(f"   输入: {repr(input_category)} -> 归一化: {repr(normalized)} (期望: {repr(expected_category)})")
    print()


def test_final_event_payload():
    """测试最终发送到Google Calendar的事件payload"""
    print("=" * 60)
    print("测试7: 最终事件payload（模拟完整流程）")
    print("=" * 60)
    
    config = load_config()
    
    category_colors = config.get('google', {}).get('category_colors')
    allowed_categories = list(category_colors.keys()) if category_colors else None
    
    parser = OpenAIEventParser(
        api_key=config['openai']['api_key'],
        default_timezone=config['assistant']['default_tz'],
        base_url=config['openai'].get('base_url'),
        text_model=config['openai']['text_model'],
        allowed_event_categories=allowed_categories,
    )
    
    default_color_id = config.get('google', {}).get('default_color_id', '')
    
    from smart_assistant.assistant import CalendarAutomationAssistant
    assistant = CalendarAutomationAssistant(
        parser=parser,
        calendar_client=None,
        category_colors=category_colors,
        default_color_id=default_color_id if default_color_id else None,
    )
    
    test_cases = [
        ("明天下午2点有个工作会议", "work"),
        ("下周三上午10点个人约会", "personal"),
        ("12月25日旅行去纽约", "travel"),
        ("下周一上午9点看医生", "medical"),  # 应该映射到medical
    ]
    
    for test_input, expected_category in test_cases:
        print(f"\n测试输入: {test_input}")
        try:
            parsed = assistant.parser.parse_text(test_input)
            if parsed.events:
                event = parsed.events[0]
                print(f"  解析后 - 类别: {repr(event.category)}, 颜色ID: {repr(event.color_id)}")
                
                # 验证类别在允许列表中
                if allowed_categories and event.category not in allowed_categories:
                    print(f"  ⚠️  警告: 类别'{event.category}'不在允许列表中!")
                else:
                    print(f"  ✓ 类别'{event.category}'在允许列表中")
                
                # 应用类别颜色（这是实际流程中会调用的）
                assistant._apply_category_color(event)
                print(f"  应用类别颜色后 - 颜色ID: {repr(event.color_id)}")
                
                # 转换为Google Calendar payload
                payload = event.to_google_body()
                color_id_in_payload = payload.get('colorId')
                print(f"  最终payload中的colorId: {repr(color_id_in_payload)}")
                
                if color_id_in_payload == "9":
                    print(f"  ⚠️  警告: 最终payload中的colorId是'9'（蓝色）")
                    if event.category == "travel":
                        print(f"     (这是正常的，因为'travel'类别映射到蓝色)")
                    else:
                        print(f"     (这可能不正常，类别'{event.category}'不应该映射到蓝色)")
                elif color_id_in_payload:
                    print(f"  ✓ 最终颜色: {color_id_in_payload}")
                else:
                    print(f"  ℹ️  没有设置颜色（Google Calendar将使用默认颜色）")
        except Exception as e:
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()
    
    print()


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("日历事件颜色调试测试")
    print("=" * 60 + "\n")
    
    try:
        # 测试1: 颜色标准化
        test_color_normalization()
        
        # 测试2: 类别颜色映射
        test_category_colors()
        
        # 测试3: GPT响应解析
        test_gpt_response_parsing()
        
        # 测试5: default_color_id处理
        test_default_color_id_handling()
        
        # 测试6: 类别归一化功能
        test_category_normalization()
        
        # 测试7: 最终事件payload
        test_final_event_payload()
        
        # 测试4: 真实GPT调用
        # 自动运行测试
        test_real_gpt_call()
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60 + "\n")
        
        # 输出诊断建议
        print("=" * 60)
        print("诊断建议")
        print("=" * 60)
        config = load_config()
        category_colors = config.get('google', {}).get('category_colors', {})
        default_color_id = config.get('google', {}).get('default_color_id', '')
        
        if not category_colors:
            print("⚠️  配置中没有category_colors，将使用代码中的DEFAULT_CATEGORY_COLORS")
        elif len(category_colors) < 5:
            print(f"⚠️  配置中只有{len(category_colors)}个类别映射，很多类别可能没有颜色")
            print("   建议: 添加更多类别到category_colors，或设置default_color_id")
        
        if default_color_id == "":
            print("⚠️  default_color_id是空字符串，未匹配的类别将没有颜色")
            print("   建议: 如果需要默认颜色，设置default_color_id为一个有效的colorId (1-11)")
        elif default_color_id == "9":
            print("⚠️  default_color_id是'9'（蓝色），这可能是所有事件都是蓝色的原因！")
            print("   建议: 如果不想默认蓝色，请修改或删除default_color_id")
        print()
        
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

