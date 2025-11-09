from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()
import os
import logging

logging.getLogger('google.generativeai').setLevel(logging.ERROR)


# 1. Configure screen dimensions for the target environment
SCREEN_WIDTH = 1920  # Increased from 1440
SCREEN_HEIGHT = 1080  # Increased from 900

# 2. Start the Playwright browser
playwright = sync_playwright().start()

# Launch with a larger window
browser = playwright.chromium.launch(
    headless=False,
    args=[
        f'--window-size={SCREEN_WIDTH},{SCREEN_HEIGHT}',
        '--start-maximized'  # Optional: maximizes the window
    ]
)

# 3. Create a context and page with the specified dimensions
context = browser.new_context(
    viewport={"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT}
)
page = context.new_page()
page.set_viewport_size({"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT})

from google import genai
from google.genai import types
from google.genai.types import Content, Part

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# Specify predefined functions to exclude (optional)
excluded_functions = ["drag_and_drop"]

from typing import Any, List, Tuple
import time

def denormalize_x(x: int, screen_width: int) -> int:
    """Convert normalized x coordinate (0-1000) to actual pixel coordinate."""
    return int(x / 1000 * screen_width)

def denormalize_y(y: int, screen_height: int) -> int:
    """Convert normalized y coordinate (0-1000) to actual pixel coordinate."""
    return int(y / 1000 * screen_height)

def click_visible_text(page, text_to_select: str):
    """
    Finds and clicks the FIRST visible element that contains
    the specified text. This is for clicking options
    in a dropdown that is ALREADY open.
    """
    try:
        print(f"Searching for VISIBLE element containing text: '{text_to_select}'")
        
        # Use regex to be flexible (e.g., "UK 6" vs " UK 6 ")
        import re
        text_regex = re.compile(re.escape(text_to_select), re.IGNORECASE)

        # Find the first element containing this text that is visible
        target_option = page.get_by_text(text_regex).filter(visible=True).first
        
        target_option.click(timeout=5000)
        print(f"Successfully clicked visible text: '{text_to_select}'")
        
    except Exception as e:
        print(f"Failed to click visible text '{text_to_select}': {e}")
        raise e
        
def execute_function_calls(candidate, page, screen_width, screen_height, user_confirmation_granted):
    results = []
    function_calls = []
    for part in candidate.content.parts:
        if part.function_call:
            function_calls.append(part.function_call)

    for function_call in function_calls:
        action_result = {}
        fname = function_call.name
        args = function_call.args
        print(f"  -> Executing: {fname}")
        
        wait_for_network = False

        try:
            if fname == "open_web_browser":
                pass # Already open
            elif fname == "click_at":
                actual_x = denormalize_x(args["x"], screen_width)
                actual_y = denormalize_y(args["y"], screen_height)
                
                decision = "PROCEED" 
                
                if 'safety_decision' in function_call.args:
                    if user_confirmation_granted:
                        print("  -> User confirmation already granted. Proceeding.")
                        decision = "PROCEED"
                        user_confirmation_granted = False # Reset the flag
                    else:
                        decision = get_safety_confirmation(function_call.args['safety_decision'])
                    action_result["safety_acknowledgement"] = "true" 
                
                if decision == "TERMINATE":
                    print("Terminating agent loop")
                    break 
                
                page.mouse.move(actual_x, actual_y)
                page.mouse.down()
                page.mouse.up()
                
                wait_for_network = True
        
            elif fname == "click_visible_text":
                text = args["text"]
                click_visible_text(page, text)
                wait_for_network = True
                
            elif fname == "type_text_at":
                actual_x = denormalize_x(args["x"], screen_width)
                actual_y = denormalize_y(args["y"], screen_height)
                text = args["text"]
                press_enter = args.get("press_enter", False)

                page.mouse.click(actual_x, actual_y)
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(text)
                if press_enter:
                    page.keyboard.press("Enter")
                    wait_for_network = True
                    
            elif fname == "scroll_document":
                direction = args["direction"]
                scroll_js = ""
                if direction == "down":
                    scroll_js = "window.scrollBy(0, window.innerHeight)"
                elif direction == "up":
                    scroll_js = "window.scrollBy(0, -window.innerHeight)"
                elif direction == "left":
                    scroll_js = "window.scrollBy(-window.innerWidth, 0)"
                elif direction == "right":
                    scroll_js = "window.scrollBy(window.innerWidth, 0)"

                if scroll_js:
                    page.evaluate(scroll_js)
                else:
                    print(f"Warning: Unknown scroll direction '{direction}'")
            
            
        except Exception as e:
            print(f"Error executing {fname}: {e}")
            action_result = {"error": str(e)}

        # This runs *after* the 'try' block but *before* the next function call
        if wait_for_network:
            try:
                print("  -> Waiting for network to be idle...")
                # Wait for up to 5 seconds for network activity to stop
                page.wait_for_load_state('networkidle', timeout=5000)
                print("  -> Network is idle.")
            except Exception as e:
                # The page might be a single-page-app that doesn't go "idle".
                # The screenshot in get_function_responses will be taken anyway.
                print(f"  -> Wait for 'networkidle' timed out. Continuing...")

        results.append((fname, action_result))

    return results, user_confirmation_granted

def get_function_responses(page, results):
    screenshot_bytes = page.screenshot(type="png")
    current_url = page.url
    function_responses = []
    for name, result in results:
        response_data = {"url": current_url}
        response_data.update(result)
        function_responses.append(
            types.FunctionResponse(
                name=name,
                response=response_data,
                parts=[types.FunctionResponsePart(
                        inline_data=types.FunctionResponseBlob(
                            mime_type="image/png",
                            data=screenshot_bytes))
                ]
            )
        )
    return function_responses

import termcolor

def get_safety_confirmation(safety_decision):
    """Prompt user for confirmation when safety check is triggered."""
    termcolor.cprint("Safety service requires explicit confirmation!", color="red")
    print(safety_decision["explanation"])

    decision = ""
    while decision.lower() not in ("y", "n", "ye", "yes", "no"):
        decision = input("Do you wish to proceed? [Y]es/[N]o\n")

    if decision.lower() in ("n", "no"):
        return "TERMINATE"
    return "CONTINUE"

user_confirmation_granted = False

try:
    # Go to initial page
    page.goto("https://alphaindustries.com")

# Configure the model
    config = types.GenerateContentConfig(
        tools=[
            # Tool 1: The built-in Computer Use tool
            types.Tool(
                computer_use=types.ComputerUse(
                    environment=types.Environment.ENVIRONMENT_BROWSER
                )
            ),
            
            # Tool 2: Our new custom function
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="click_visible_text",
                        description="Selects an option from a dropdown menu that is ALREADY OPEN. Use this on the turn AFTER you have used 'click_at' to open a dropdown.",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "text": types.Schema(type=types.Type.STRING, description="The text of the option to click (e.g., 'UK 6', 'Small').")
                            },
                            required=["text"]
                        )
                    )
                ]
            )
        ],
        thinking_config=types.ThinkingConfig(include_thoughts=True),
    )

    # Initialize history
    initial_screenshot = page.screenshot(type="png")
    TASK_INSTRUCTION = """Go to alphaindustries.com and find me a bomber jacket NASA.
    In the meantime, close any pop ups that show up but do not leave the page.
    After you search it, ask the user to choose which one he wants to buy.
    After the user chooses the model, click to add to cart.
    Finally, proceed to checkout."""
    
    RULES = """## **RULE 1: Seek User Confirmation (USER_CONFIRMATION)**

    This is your first and most important check. If the next required action falls
    into any of the following categories, you MUST stop immediately, and seek the
    user's explicit permission.

    **Procedure for Seeking Confirmation:** * **For Consequential Actions:**
    Perform all preparatory steps (e.g., navigating, filling out forms, typing a
    message). You will ask for confirmation **AFTER** all necessary information is
    entered on the screen, but **BEFORE** you perform the final, irreversible action
    (e.g., before clicking "Send", "Submit", "Confirm Purchase", "Share").  * **For
    Prohibited Actions:** If the action is strictly forbidden (e.g., accepting legal
    terms, solving a CAPTCHA), you must first inform the user about the required
    action and ask for their confirmation to proceed.

    **USER_CONFIRMATION Categories:**

    * **Consent and Agreements:** You are FORBIDDEN from accepting, selecting, or
        agreeing to any of the following on the user's behalf. You must ask the
        user to confirm before performing these actions.
        * Terms of Service
        * Privacy Policies
        * Cookie consent banners
        * End User License Agreements (EULAs)
        * Any other legally significant contracts or agreements.
    * **Robot Detection:** You MUST NEVER attempt to solve or bypass the
        following. You must ask the user to confirm before performing these actions.
    * CAPTCHAs (of any kind)
        * Any other anti-robot or human-verification mechanisms, even if you are
            capable.
    * **Financial Transactions:**
        * Completing any purchase.
        * Managing or moving money (e.g., transfers, payments).
        * Purchasing regulated goods or participating in gambling.
    * **Sending Communications:**
        * Sending emails.
        * Sending messages on any platform (e.g., social media, chat apps).
        * Posting content on social media or forums.
    * **Accessing or Modifying Sensitive Information:**
        * Health, financial, or government records (e.g., medical history, tax
            forms, passport status).
        * Revealing or modifying sensitive personal identifiers (e.g., SSN, bank
            account number, credit card number).
    * **User Data Management:**
        * Accessing, downloading, or saving files from the web.
        * Sharing or sending files/data to any third party.
        * Transferring user data between systems.
    * **Browser Data Usage:**
        * Accessing or managing Chrome browsing history, bookmarks, autofill data,
            or saved passwords.
    * **Security and Identity:**
        * Logging into any user account.
        * Any action that involves misrepresentation or impersonation (e.g.,
            creating a fan account, posting as someone else).
    * **Insurmountable Obstacles:** If you are technically unable to interact with
        a user interface element or are stuck in a loop you cannot resolve, ask the
        user to take over.
    
    # Final Response Guidelines:
    Write final response to the user in the following cases:
    - User confirmation
    - When the task is complete or you have enough information to respond to the user"""

    # PROMPT CHAINING
    USER_PROMPT = f"{TASK_INSTRUCTION}\n\n{RULES}"
    contents = [
        Content(role="user", parts=[
            Part(text=USER_PROMPT),
            Part.from_bytes(data=initial_screenshot, mime_type='image/png')
        ])
    ]

    # Agent Loop
    turn_limit = 50
    for i in range(turn_limit):
        print(f"\n--- Turn {i+1} ---")
        print("Thinking...")
        response = client.models.generate_content(
            model='gemini-2.5-computer-use-preview-10-2025',
            contents=contents,
            config=config,
        )

        candidate = response.candidates[0]
        contents.append(candidate.content)

        has_function_calls = any(part.function_call for part in candidate.content.parts)
        if not has_function_calls:
            # The model returned a text response, likely a question or a final answer.
            text_response = " ".join([part.text for part in candidate.content.parts if part.text])
            print(f"\n🤖 Agent: {text_response}")

            # Don't break. Get the user's follow-up instruction.
            user_follow_up = input("\nYour response (or 'quit'): ") 
            
            if user_follow_up.lower() in ["y", "yes", "yeah", "proceed", "ok"]:
                user_confirmation_granted = True
            elif user_follow_up.lower() in ["quit", "exit"]:
                print("Exiting loop.")
                break # Now we break because the user said to
            

            print("Capturing state for user response...")
            # Get a fresh screenshot to go with the user's new instruction
            screenshot_bytes = page.screenshot(type="png")

            contextual_response = f"""
            My response is: '{user_follow_up}'
            
            Please use this information to continue the original task:
            {TASK_INSTRUCTION}
            """


            # Add the user's new instruction and the latest screenshot to history
            contents.append(
                Content(role="user", parts=[
                    Part(text=contextual_response), # Use the new contextual response
                    Part.from_bytes(data=screenshot_bytes, mime_type='image/png')
                ])
            )
            
            # Continue to the next iteration of the loop
            continue 
        # (The rest of your loop for handling function calls remains the same)
        print("Executing actions...")
        results, user_confirmation_granted = execute_function_calls(
            candidate, page, SCREEN_WIDTH, SCREEN_HEIGHT, user_confirmation_granted
        )

        print("Capturing state...")
        function_responses = get_function_responses(page, results)

        contents.append(
            Content(role="user", parts=[Part(function_response=fr) for fr in function_responses])
        )

finally:
    # Cleanup
    print("\nClosing browser...")
    browser.close()
    playwright.stop()
