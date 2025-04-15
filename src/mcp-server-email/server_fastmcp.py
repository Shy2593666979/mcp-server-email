import argparse
import asyncio
import os
import json
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Optional, Union, Set
from mcp import McpError, GetPromptResult, stdio_server
from mcp.server import Server, FastMCP
from mcp.types import (Tool, ErrorData, Prompt, PromptArgument, PromptMessage,
                       TextContent, INVALID_PARAMS, INTERNAL_ERROR, METHOD_NOT_FOUND)

from pydantic import BaseModel, Field
from dotenv import load_dotenv

logger = logging.getLogger('mcp_email_server')
logger.info("Starting MCP Email Server")


load_dotenv()

sender = os.getenv("SENDER")
password = os.getenv("PASSWORD")

# Get the directory where the service is started
server_dir = os.path.dirname(os.path.abspath(__file__))

mcp = FastMCP("MCP-Email")

def initialization_email_config():

    with open(os.path.join(server_dir, "email.json"), "r", encoding="UTF-8") as file:
        return json.load(file)

email_config = initialization_email_config()

ATTACHMENT_FOLDER = None

class EmailMessage(BaseModel):
    receiver: list[str] = Field(description="The list of recipient email addresses, supports multiple recipients")
    body: str = Field(description="The main content of the email")
    subject: str = Field(description="The subject line of the email")
    attachments: Union[list[str], str] = Field(default=[], description="Email attachments, just need to get the file name of the attachment")

class EmailTools(str, Enum):
    SEND_EMAIL = "send_email"
    SEARCH_ATTACHMENTS = "search_attachments"


async def send_email(email_message: EmailMessage):
    """Send email asynchronously.

    Args:
        email_message (EmailMessage): Email message object, including email content, recipients, etc.

    Returns:
        str: Returns the message sent by email
    """
    smtp_server, smtp_port = get_smtp_info()
    if not (smtp_server and smtp_port):
        raise ValueError("Please check that your email address is entered correctly, or it is not a supported email service")
    logger.info(f"send email message: {email_message.model_json_schema()}")
    # Build email content
    message = MIMEMultipart()
    message["From"] = sender
    message["To"] = ", ".join(email_message.receiver)  # Convert the recipient list to a comma-delimited string
    message["Subject"] = email_message.subject

    message.attach(MIMEText(email_message.body, "plain"))

    if isinstance(email_message.attachments, str):
        try:
            email_message.attachments = email_message.attachments.replace("\\", "\\\\")
            attachments = json.loads(email_message.attachments)
        except Exception as e:
            raise ValueError(f"email message attachments error: {e}")
        else:
            email_message.attachments = attachments

    # Attach attachments to email messages
    if email_message.attachments:
        for file in email_message.attachments:
            absolute_path = os.path.join(ATTACHMENT_FOLDER, file)
            if os.path.isfile(absolute_path):
                message.attach(attach_file(absolute_path))
            else:
                raise ValueError(f"{absolute_path} not exists")

    try:
        # create SMTP server
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()  # Send a greeting command to the server
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                server.starttls()
            server.login(sender, password)  # Using application-specific passwords
            server.sendmail(sender, email_message.receiver, message.as_string())
        return f"Email sent successfully from sender: {sender}"
    except Exception as e:
        raise ValueError(str(e))



def get_smtp_info() -> tuple[str, int] | tuple[None, None]:
    """Get the SMTP server address and port from the configuration based on the sender's email domain name.

    Returns:
        tuple[str, int] | tuple[None, None]: Returns the matching SMTP server address and port; if not found, returns (None, None).
    """
    # Extract the domain name part of the sender's email
    domain = f"@{sender.split('@')[1]}"

    # Traverse the configuration and find the matching domain name
    for config in email_config:
        if config.get("domain") == domain:
            return config.get("server"), config.get("port")

    # No matching configuration found
    return None, None


def get_lack_params(arguments: dict) -> str | None:
    """Check if the parameters dictionary is missing a required field for the EmailMessage model.

    Args:
        arguments (dict): The supplied parameter dictionary.

    Returns:
        str | None: If a field is missing, returns an error message for the missing field; otherwise returns None.
    """
    # Get the field names of the EmailMessage model
    required_fields = EmailMessage.model_fields.keys()

    # Find the missing fields
    missing_fields = [field for field in required_fields if field not in arguments]

    # Logging
    logger.info(f"Missing params: {missing_fields}")

    # If there is a missing field, returns an error message; otherwise returns None
    if missing_fields:
        return "\n".join(f"{field} is required" for field in missing_fields)
    return None


async def search_attachments(pattern: str, ignore_case: bool = True) -> str:
    """
    Searches for file paths matching the specified pattern in the specified directory.

    Args:
        pattern: the text pattern to search
        ignore_case: whether to ignore case

    Returns:
        A collection of matching file paths
    """
    # 规范化目录路径
    root_dir = os.path.abspath(os.path.expanduser(ATTACHMENT_FOLDER))
    if not os.path.exists(root_dir):
        raise ValueError(f"directory no exists: {ATTACHMENT_FOLDER}")

    matches = set()
    pattern = pattern.lower() if ignore_case else pattern

    # 遍历目录
    for root, _, files in os.walk(root_dir):
        for file in files:
            file_name = file.lower() if ignore_case else file
            if pattern in file_name:
                full_path = os.path.join(root, file)
                matches.add(full_path)

    return "\n".join(matches)

def attach_file(file_path):
    # Define allowed file types
    ALLOWED_EXTENSIONS = {
        'document': ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'pdf'],
        'archive': ['zip', 'rar', '7z', 'tar', 'gz'],
        'text': ['txt', 'log', 'csv', 'json', 'xml'],
        'image': ['jpg', 'jpeg', 'png', 'gif', 'bmp'],
        'other': ['md']  # Other special formats allowed
    }

    # Flatten the list of allowed extensions
    allowed_extensions = [ext for exts in ALLOWED_EXTENSIONS.values() for ext in exts]

    with open(file_path, 'rb') as f:
        file_data = f.read()
        filename = os.path.basename(file_path)
        ext = filename.lower().split('.')[-1]

        # Check if the file type is allowed
        if ext not in allowed_extensions:
            raise ValueError(f"Unsupported file types: {ext}")

        # Process according to file type
        if ext in ALLOWED_EXTENSIONS['document']:
            attachment = MIMEApplication(file_data, _subtype=ext)
        elif ext in ALLOWED_EXTENSIONS['archive']:
            attachment = MIMEApplication(file_data)
        elif ext in ALLOWED_EXTENSIONS['text']:
            try:
                attachment = MIMEText(file_data.decode('UTF-8'), 'plain')
            except UnicodeDecodeError:
                # If UTF-8 decoding is not possible, treat it as a normal attachment
                attachment = MIMEApplication(file_data)
        elif ext in ALLOWED_EXTENSIONS['image']:
            attachment = MIMEImage(file_data)
        else:
            # Other allowed formats use generic processing
            attachment = MIMEApplication(file_data)

        attachment.add_header('Content-Disposition', 'attachment', filename=filename)
        return attachment



@mcp.tool()
async def send_email_tool(receiver: list, body: str, subject: str, attachments: list = []):
    """A function call tool designed to send emails based on the provided recipients, subject, body, and optional attachments. This tool ensures secure and accurate email delivery while supporting multiple recipients and customizable content. It is ideal for automating email workflows.
    After gathering the necessary information (recipients, subject, body, and attachments), the tool displays the composed email details to the user for review. The email is only sent after the user confirms the details, ensuring accuracy and intentionality in communication.

    Args:
        receiver: The list of recipient email addresses, supports multiple recipients.
        body: The main content of the email.
        subject: The subject line of the email.
        attachments: Email attachments, just need to get the file name of the attachment.
    """
    try:
        args = EmailMessage(receiver=receiver, body=body, subject=subject, attachments=attachments)
    except ValueError as e:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(e)))
    try:
        email_response = await send_email(args)
        return [TextContent(type="text", text=f"Send email response: \n{email_response}")]
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))

@mcp.tool()
async def search_attachments_tool(pattern: str):
    """Searches for files in a specified directory that match a given pattern. The search can be case-insensitive and returns the full paths of all matching files.
    This tool is useful for locating specific files or attachments within a directory structure.

    Args:
        pattern: The text pattern to search for in file names. The search is case-insensitive by default.
    """
    try:
        search_response = await asyncio.wait_for(search_attachments(pattern=pattern), timeout=3)
        return [TextContent(type="text", text=f"Search attachments response: \n{search_response}")]
    except asyncio.TimeoutError as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))
    except Exception as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e)))


if __name__ == "__main__":
    # Initialize and run the server
    parser = argparse.ArgumentParser("Search files in a specified directory")
    parser.add_argument("--dir", type=str, help="Specify the directory where attachments are located.")

    args = parser.parse_args()
    ATTACHMENT_FOLDER = args.dir


    mcp.run(transport='stdio')