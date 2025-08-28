import gradio as gr
import os
import json
import subprocess
import socket
from pathlib import Path
from contextlib import closing
from typing import List, Optional, Tuple
import re
import webbrowser
import logging
import traceback
from datetime import datetime
import sys
import signal
import time
import shutil

# ======================== 初始化设置 ========================
os.environ['GRADIO_SERVER_NAME'] = '127.0.0.1'
os.environ['PYTHONUTF8'] = '1'  # 确保UTF-8编码
if os.name == 'nt':
    os.environ['PYTHONLEGACYWINDOWSSTDIO'] = 'utf-8'
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ======================== 日志配置 ========================
LOG_FILE = "assfontsubset_gui.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# ======================== 配置管理 ========================
DEFAULT_CONFIG_PATH = os.path.join(os.getcwd(), "config.json")
PORT_FILE_PATH = os.path.join(os.getcwd(), "WebUI_Port.txt")
DEFAULT_PORT = 7888
DEFAULT_CONFIG = {
    "input_paths": [],
    "output_dir": "",
    "font_dir": "",
    "subset_backend": "PyFontTools",
    "bin_path": "",
    "source_han_ellipsis": True,
    "debug": False,
    "server_port": DEFAULT_PORT
}

def clean_path(path_str: str) -> str:
    """清理路径字符串，去除多余的引号"""
    return re.sub(r'^[\'"]|[\'"]$', '', path_str.strip()) if path_str else ""

def validate_dir_path(path_str: str) -> Tuple[bool, Optional[str]]:
    """验证目录路径是否有效"""
    path_str = clean_path(path_str)
    if not path_str:
        return False, "路径不能为空"
    try:
        path = Path(path_str)
        if path.is_dir():
            return True, str(path.resolve())
        return False, "路径不是有效目录"
    except Exception as e:
        return False, f"路径验证失败: {str(e)}"

def validate_port(port_str: str) -> Tuple[bool, Optional[int]]:
    """验证端口号是否有效"""
    try:
        port = int(port_str)
        if 1024 <= port <= 65535:
            return True, port
        return False, None
    except ValueError:
        return False, None

def get_port_from_file():
    """从WebUI_Port文件中读取端口号"""
    try:
        if os.path.exists(PORT_FILE_PATH):
            with open(PORT_FILE_PATH, 'r', encoding='utf-8') as f:
                port_str = f.read().strip()
                is_valid, port = validate_port(port_str)
                if is_valid:
                    return port
        return None
    except Exception as e:
        logging.error(f"读取端口文件出错: {str(e)}")
        return None

def save_port_to_file(port):
    """保存端口号到WebUI_Port文件"""
    try:
        is_valid, port = validate_port(str(port))
        if not is_valid:
            return False
        
        with open(PORT_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(str(port))
        return True
    except Exception as e:
        logging.error(f"保存端口文件出错: {str(e)}")
        return False

def generate_default_filename() -> str:
    """生成默认文件名"""
    return f"assfont_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

def load_config(config_path: str = "") -> Tuple[dict, Optional[str]]:
    """加载配置文件"""
    config_path = clean_path(config_path) if config_path else DEFAULT_CONFIG_PATH
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                if isinstance(config, dict):
                    valid_config = DEFAULT_CONFIG.copy()
                    for key, value in config.items():
                        if key in valid_config:
                            if key.endswith(('_dir', '_path', 'output_dir')):
                                # 对于输出目录，允许为空字符串
                                if key == 'output_dir':
                                    valid_config[key] = value if value is not None else ""
                                else:
                                    validated = validate_dir_path(value)[1]
                                    if validated:
                                        valid_config[key] = validated
                            elif key == 'input_paths' and isinstance(value, list):
                                valid_config[key] = [validate_dir_path(p)[1] for p in value if validate_dir_path(p)[1]]
                            else:
                                valid_config[key] = value
                    return valid_config, None
        return DEFAULT_CONFIG.copy(), None
    except Exception as e:
        error_msg = f"加载配置文件出错: {str(e)}"
        logging.error(error_msg)
        return DEFAULT_CONFIG.copy(), error_msg

def save_config(save_dir: str, filename: str, config: dict) -> Tuple[bool, Optional[str]]:
    """保存配置到指定路径"""
    try:
        is_valid, validated_dir = validate_dir_path(save_dir)
        if not is_valid:
            return False, validated_dir
        
        if not filename.strip():
            filename = generate_default_filename()
        elif not filename.lower().endswith('.json'):
            filename += '.json'
        
        save_path = os.path.join(validated_dir, filename)
        os.makedirs(validated_dir, exist_ok=True)
        
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        return True, f"配置已成功保存到: {save_path}"
    except Exception as e:
        error_msg = f"保存配置文件出错: {str(e)}"
        logging.error(error_msg)
        return False, error_msg

def run_assfontsubset(input_paths: List[str], output_dir: str, font_dir: str, 
                     subset_backend: str, bin_path: str, 
                     source_han_ellipsis: bool, debug: bool) -> str:
    """执行 AssFontSubset 命令"""
    try:
        valid_inputs = []
        for p in input_paths:
            p = clean_path(p)
            if p.lower().endswith('.ass') and os.path.isfile(p):
                valid_inputs.append(p)
        
        if not valid_inputs:
            return "错误：没有有效的ASS文件路径"
        
        # 处理输出目录 - 运行时不允许为空
        final_output_dir = output_dir
        if not final_output_dir or not final_output_dir.strip():
            # 如果输出目录为空，使用第一个输入文件所在目录下的output文件夹
            first_input_dir = os.path.dirname(valid_inputs[0])
            final_output_dir = os.path.join(first_input_dir, "output")
        
        # 确保输出目录存在
        os.makedirs(final_output_dir, exist_ok=True)
        
        cmd = ["./AssFontSubset.Console"]
        cmd.extend(valid_inputs)
        
        if final_output_dir:
            cmd.extend(["--output", final_output_dir])
        
        if font_dir:
            is_valid, validated = validate_dir_path(font_dir)
            if is_valid:
                cmd.extend(["--fonts", validated])
        
        if subset_backend != "PyFontTools":
            cmd.extend(["--subset-backend", subset_backend])
        
        if bin_path and bin_path.strip():
            is_valid, validated = validate_dir_path(bin_path)
            if is_valid:
                cmd.extend(["--bin-path", validated])
        
        if not source_han_ellipsis:
            cmd.append("--no-source-han-ellipsis")
        
        if debug:
            cmd.append("--debug")
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8',
            errors='replace',
            check=True
        )
        
        output = f"执行成功！\n\n输出信息：\n{result.stdout}"
        if debug and result.stderr:
            output += f"\n\n调试信息：\n{result.stderr}"
        return output
    except subprocess.CalledProcessError as e:
        error_msg = f"执行出错：\n\n命令: {' '.join(cmd)}\n错误: {e.stderr}"
        logging.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"发生异常：\n\n{str(e)}\n\n{traceback.format_exc()}"
        logging.error(error_msg)
        return error_msg

def create_ui():
    initial_port = get_port_from_file() or DEFAULT_PORT
    
    with gr.Blocks(title="AssFontSubset WebUI") as demo:
        with gr.Tab("主界面"):
            gr.Markdown("# AssFontSubset 字体子集化工具")
            
            with gr.Row():
                with gr.Column():
                    with gr.Accordion("服务器设置", open=False):
                        server_port = gr.Number(
                            label="服务器端口",
                            value=initial_port,
                            precision=0,
                            minimum=1024,
                            maximum=65535
                        )
                        port_save_btn = gr.Button("保存端口设置", variant="secondary")
                        port_status = gr.Textbox(label="状态", interactive=False)
                    
                    with gr.Group():
                        config_file = gr.File(label="选择配置文件", file_types=[".json"], file_count="single")
                        load_config_btn = gr.Button("加载配置")
                    
                    with gr.Group():
                        save_dir = gr.Textbox(label="保存目录", placeholder="输入保存目录完整路径")
                        filename = gr.Textbox(label="文件名（可选）", placeholder="留空使用自动生成文件名")
                        save_btn = gr.Button("保存配置", variant="primary")
                    
                    input_files = gr.Files(
                        label="选择ASS字幕文件",
                        file_types=[".ass"],
                        file_count="multiple"
                    )
                    output_dir = gr.Textbox(label="输出目录 (默认: 同目录下的output文件夹)")
                    font_dir = gr.Textbox(label="字体目录 (默认: 同目录下的fonts文件夹)")
                    
                    with gr.Accordion("高级选项", open=False):
                        subset_backend = gr.Dropdown(
                            ["PyFontTools", "HarfBuzzSubset"], 
                            label="子集化后端",
                            value="PyFontTools"
                        )
                        bin_path = gr.Textbox(
                            label="pyftsubset和ttx所在目录 (可选)",
                            placeholder="留空则使用系统默认路径"
                        )
                        source_han_ellipsis = gr.Checkbox(
                            label="思源黑体/宋体省略号居中对齐",
                            value=True
                        )
                        debug = gr.Checkbox(label="调试模式", value=False)
                    
                    run_btn = gr.Button("开始子集化", variant="primary")
                
                with gr.Column():
                    output_log = gr.Textbox(label="执行结果", interactive=False, lines=20)
        
        with gr.Tab("控制台日志"):
            log_display = gr.Textbox(label="日志内容", interactive=False, lines=25, max_lines=1000)
            refresh_btn = gr.Button("刷新日志")
            
            def update_log_display():
                try:
                    if os.path.exists(LOG_FILE):
                        with open(LOG_FILE, 'r', encoding='utf-8') as f:
                            return f.read()
                    return "暂无日志内容"
                except Exception as e:
                    return f"读取日志失败: {str(e)}"
            
            refresh_btn.click(update_log_display, outputs=log_display)
        
        def handle_load_config(config_file_obj):
            if not config_file_obj:
                return [], "", "", "PyFontTools", "", True, False, initial_port, "未选择配置文件"
            
            try:
                config_path = config_file_obj.name
                config, error = load_config(config_path)
                if error:
                    return [], "", "", "PyFontTools", "", True, False, initial_port, error
                
                return (
                    config["input_paths"],
                    config["output_dir"],
                    config["font_dir"],
                    config["subset_backend"],
                    config["bin_path"],
                    config["source_han_ellipsis"],
                    config["debug"],
                    config.get("server_port", initial_port),
                    None
                )
            except Exception as e:
                error_msg = f"加载配置文件出错: {str(e)}"
                logging.error(error_msg)
                return [], "", "", "PyFontTools", "", True, False, initial_port, error_msg
        
        def handle_save_config(save_dir, filename, input_files, output_dir, font_dir, 
                             subset_backend, bin_path, source_han_ellipsis, debug, port):
            current_config = {
                "input_paths": input_files,
                "output_dir": output_dir,
                "font_dir": font_dir,
                "subset_backend": subset_backend,
                "bin_path": bin_path,
                "source_han_ellipsis": source_han_ellipsis,
                "debug": debug,
                "server_port": port
            }
            success, result = save_config(save_dir, filename, current_config)
            return result
        
        def save_port_settings(port):
            try:
                port = int(port)
                if 1024 <= port <= 65535:
                    save_port_to_file(port)
                    return f"端口设置已保存: {port} (下次启动时生效)", ""
                return "端口号必须在1024-65535之间", ""
            except ValueError:
                return "请输入有效的端口号", ""
        
        load_config_btn.click(
            lambda: update_log_display(),
            outputs=log_display
        ).then(
            handle_load_config,
            inputs=config_file,
            outputs=[input_files, output_dir, font_dir, subset_backend, bin_path, 
                    source_han_ellipsis, debug, server_port, output_log]
        )
        
        save_btn.click(
            handle_save_config,
            inputs=[save_dir, filename, input_files, output_dir, font_dir, 
                   subset_backend, bin_path, source_han_ellipsis, debug, server_port],
            outputs=output_log
        )
        
        port_save_btn.click(
            save_port_settings,
            inputs=server_port,
            outputs=[port_status, output_log]
        )
        
        run_btn.click(
            run_assfontsubset,
            inputs=[input_files, output_dir, font_dir, subset_backend, bin_path, source_han_ellipsis, debug],
            outputs=output_log
        ).then(
            lambda: update_log_display(),
            outputs=log_display
        )
        
        return demo

def safe_launch(demo, max_attempts=20):
    """安全启动Gradio应用"""
    preferred_port = get_port_from_file() or DEFAULT_PORT
    
    for attempt in range(max_attempts):
        port = preferred_port + attempt
        try:
            print(f"尝试在端口 {port} 启动...")
            
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                s.bind(('127.0.0.1', port))
            
            # 设置信号处理
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            
            demo.launch(
                server_name="127.0.0.1",
                server_port=port,
                show_error=True
            )
            
            save_port_to_file(port)
            print(f"服务已启动在端口 {port}")
            webbrowser.open(f"http://127.0.0.1:{port}")
            return port
        except OSError as e:
            print(f"端口 {port} 不可用: {e}")
            if attempt == max_attempts - 1:
                print("尝试使用分享模式...")
                demo.launch(share=True)
                return None

def main():
    try:
        demo = create_ui()
        safe_launch(demo)
    except KeyboardInterrupt:
        print("\n程序已安全退出")
        sys.exit(0)
    except Exception as e:
        logging.error(f"程序启动失败: {str(e)}\n{traceback.format_exc()}")
        print(f"程序启动失败: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
