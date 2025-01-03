import dotenv
import json
import requests

from openai import AzureOpenAI
import streamlit as st
from streamlit_chat import message
import openai
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Load environment variables
ENV = dotenv.dotenv_values(".env")
with st.sidebar.expander("Environment Variables"):
    st.write(ENV)

ICD_KEY = ENV['ICD_KEY']
ICD_CLIENT = ENV['ICD_CLIENT']

def get_secret_from_key_vault(vault_url, secret_name):
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=vault_url, credential=credential)
    secret = client.get_secret(secret_name)
    return secret.value

def get_bearer_token():
    token_url = "https://icdaccessmanagement.who.int/connect/token"
    payload = {
        'client_id': ICD_CLIENT,
        'client_secret': ICD_KEY,
        'scope': 'icdapi_access',
        'grant_type': 'client_credentials'
    }
    response = requests.post(token_url, data=payload)
    if response.status_code == 200:
        return response.json().get('access_token')
    else:
        st.error("Failed to obtain bearer token.")
        return None

def generate_authenticated_url(base_url, token):
    return f"{base_url}?access_token={token}"

def query_icd_11_api(input):
    token = get_bearer_token()
    if not token:
        return None

    code_mappings = {}
    for word in input.split():
        code_mappings[word] = {}
        word = word.replace(",", "")
        url = f"https://id.who.int/icd/release/11/2019-04/mms/search?q={word}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            'API-Version': 'v2',
            'Accept-Language': 'en'
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            potential_codes = response.json()['destinationEntities']
            for code_ in potential_codes:
                code = code_.get('theCode')
                link = code_.get('id')
                authenticated_link = generate_authenticated_url(link, token)
                code_mappings[word].update({code: authenticated_link})
        else:
            st.error(f"Failed to query ICD-11 API for word: {word}")
    return json.dumps(code_mappings)

# Set up Key Vault details
key_vault_url = os.getenv('AZURE_KEY_VAULT_URL')
if not key_vault_url:
    st.error("AZURE_KEY_VAULT_URL environment variable is not set.")
    st.stop()

# Retrieve secrets
azure_openai_key = get_secret_from_key_vault(key_vault_url, 'AZUREOPENAIKEY')
azure_openai_endpoint = get_secret_from_key_vault(key_vault_url, 'AZUREOPENAIENDPOINT')
azure_openai_deployment_name = get_secret_from_key_vault(key_vault_url, 'AZUREOPENAIDEPLOYMENTNAME')
icd_client_id = get_secret_from_key_vault(key_vault_url, 'ICDCLIENT')
icd_client_secret = get_secret_from_key_vault(key_vault_url, 'ICDKEY')

# Initialize session state
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "generated" not in st.session_state:
    st.session_state["generated"] = []
if "past" not in st.session_state:
    st.session_state["past"] = []
if "model_name" not in st.session_state:
    st.session_state["model_name"] = []

def generate_response(prompt):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "query_icd_11_api",
                "description": "Function to call the ICD-11 API and return suggestions for medical codes based on the user input. It returns a dictionary that contains the input word and the corresponding ICD-11 code and web link.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                        },
                    },
                },
            },
        }
    ]
    st.session_state["messages"].append({"role": "user", "content": prompt})
    completion = None  # Initialize the variable
    try:
        client = AzureOpenAI(api_key=azure_openai_key, api_version="2024-02-01", azure_endpoint=azure_openai_endpoint)
        completion = client.chat.completions.create(
            model=azure_openai_deployment_name,
            messages=st.session_state["messages"],
            tools=tools
        )
        response = completion.choices[0].message.content
        tool_calls = completion.choices[0].finish_reason
        if tool_calls == "tool_calls":
            available_functions = {
                "query_icd_11_api": query_icd_11_api,
            }
            st.session_state["messages"].append(completion.choices[0].message)
            function = completion.choices[0].message.tool_calls[0].function
            function_name = function.name
            function_to_call = available_functions[function_name]
            function_args = json.loads(function.arguments)
            tool_response = function_to_call(
                input=function_args.get("input"),
            )
            st.session_state["messages"].append(
                {
                    "role": "function",
                    "name": function_name,
                    "content": tool_response,
                }
            )
            # Send the tool response back to the API
            completion = client.chat.completions.create(
                model=azure_openai_deployment_name,
                messages=st.session_state["messages"],
                tools=tools
            )
            response = completion.choices[0].message.content
            st.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )
        else:
            st.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": response,
                }
            )
        total_tokens = completion.usage.total_tokens
        prompt_tokens = completion.usage.prompt_tokens
        completion_tokens = completion.usage.completion_tokens
        return response, total_tokens, prompt_tokens, completion_tokens
    except Exception as e:
        st.error(f"An error occurred: {e}")
        return None, None, None, None

# Streamlit UI
st.title("Clinical Coding with OpenAI")
st.header("Enter your prompt below:")

# Text input for the prompt
prompt = st.text_area("Prompt", height=150)

# Button to submit the prompt
if st.button("Get Response"):
    if prompt:
        with st.spinner("Generating response..."):
            response, total_tokens, prompt_tokens, completion_tokens = generate_response(prompt)
            if response:
                st.success("Response generated!")
                st.text_area("Response", value=response, height=150)
    else:
        st.error("Please enter a prompt.")

# Additional UI elements
st.sidebar.title("Settings")
st.sidebar.write("Adjust your settings here.")

# Example of a slider
max_tokens = st.sidebar.slider("Max Tokens", min_value=50, max_value=500, value=150)

# Example of a checkbox
show_raw_response = st.sidebar.checkbox("Show raw response")

# Display raw response if checkbox is checked
if show_raw_response and 'response' in locals():
    st.sidebar.write(response)

# container for chat history
response_container = st.container()
# container for text box
container = st.container()

with container:
    with st.form(key="my_form", clear_on_submit=True):
        user_input = st.text_area("You:", key="input", height=100)
        submit_button = st.form_submit_button(label="Send")

    if submit_button and user_input:
        response, total_tokens, prompt_tokens, completion_tokens = generate_response(user_input)
        st.session_state["past"].append(user_input)
        st.session_state["generated"].append(response)
        st.session_state["model_name"].append(azure_openai_deployment_name)

if st.session_state["generated"]:
    with response_container:
        for i in range(len(st.session_state["generated"])):
            message(f"User: {st.session_state['past'][i]}")
            message(f"Response: {st.session_state['generated'][i]}")