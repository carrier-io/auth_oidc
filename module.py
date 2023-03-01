#!/usr/bin/python3
# coding=utf-8

#   Copyright 2022 getcarrier.io
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

""" Module """

import json
import time
import uuid
import base64
import urllib
import textwrap
import datetime

import requests
import flask  # pylint: disable=E0611,E0401
import jwt  # pylint: disable=E0401

from pylon.core.tools import log  # pylint: disable=E0611,E0401
from pylon.core.tools import web  # pylint: disable=E0611,E0401
from pylon.core.tools import module  # pylint: disable=E0611,E0401

from tools import auth

from . import tools


class Module(module.ModuleModel):
    """ Pylon module """

    def __init__(self, context, descriptor):
        self.context = context
        self.descriptor = descriptor

    #
    # Module
    #

    def init(self):
        """ Init module """
        log.info("Initializing module")
        # Init blueprint
        self.descriptor.init_blueprint(
            url_prefix=self.descriptor.config.get("url_prefix", None)
        )
        # Register auth provider
        self.context.rpc_manager.call.auth_register_auth_provider(
            "oidc",
            login_route="auth_oidc.login",
            logout_route="auth_oidc.logout",
        )
        #
        # metadata = requests.get(self.descriptor.config["issuer"]).json()
        # log.info("[Metadata]: %s", metadata)

    def deinit(self):  # pylint: disable=R0201
        """ De-init module """
        log.info("De-initializing module")
        # Unregister auth provider
        self.context.rpc_manager.call.auth_unregister_auth_provider("oidc")

    #
    # Routes
    #

    @web.route("/login")
    def login(self):
        """ Login """
        target_token = flask.request.args.get("target_to", "")
        #
        if "auth_oidc" not in flask.session:
            flask.session["auth_oidc"] = dict()
        #
        while True:
            state_uuid, target_state = tools.generate_state_id(self)
            if state_uuid not in flask.session["auth_oidc"]:
                break
        #
        flask.session["auth_oidc"][state_uuid] = dict()
        flask.session["auth_oidc"][state_uuid]["target_token"] = target_token
        flask.session.modified = True
        #
        return self.descriptor.render_template(
            "redirect.html",
            action=self.descriptor.config["authorization_endpoint"],
            parameters=[
                {
                    "name": "response_type",
                    "value": "code",
                },
                {
                    "name": "client_id",
                    "value": self.descriptor.config["client_id"],
                },
                {
                    "name": "redirect_uri",
                    "value": flask.url_for("auth_oidc.login_callback"),
                },
                {
                    "name": "scope",
                    "value": "openid profile email",
                },
                {
                    "name": "state",
                    "value": target_state,
                },
            ],
        )

    @web.route("/login_callback")
    def login_callback(self):  # pylint: disable=R0912,R0914,R0915
        """ Login callback """
        log.info("GET arguments: %s", flask.request.args)
        #
        if "state" not in flask.request.args:
            log.error("No state in OIDC callback")
            return auth.access_denied_reply()
        #
        target_state = flask.request.args["state"]
        #
        try:
            state_uuid = tools.get_state_id(self, target_state)
            if state_uuid not in flask.session["auth_oidc"]:
                raise ValueError("Unknown state")
        except:  # pylint: disable=W0702
            log.error("Invalid state")
            return auth.access_denied_reply()
        #
        oidc_state = flask.session["auth_oidc"].pop(state_uuid)
        flask.session.modified = True
        #
        target_token = oidc_state.get("target_token", "")
        #
        if "code" not in flask.request.args:
            log.error("No code in OIDC callback")
            return auth.access_denied_reply()
        #
        oidc_code = flask.request.args["code"]
        #
        try:
            oidc_token = requests.post(
                self.descriptor.config["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": oidc_code,
                    "redirect_uri": flask.url_for("auth_oidc.login_callback"),
                },
                auth=(
                    self.descriptor.config["client_id"],
                    self.descriptor.config["client_secret"],
                ),
                verify=self.descriptor.config.get("token_endpoint_verify", True),
            ).json()
        except:  # pylint: disable=W0702
            log.error("Failed to get token")
            return auth.access_denied_reply()
        #
        log.info("Token: %s", oidc_token)
        #
        if "error" in oidc_token:
            log.error("Error in OIDC token: %s", oidc_token.get("error_description", "unknown"))
            return auth.access_denied_reply()
        #
        if "id_token" not in oidc_token:
            log.error("Invalid OIDC token: no id_tokeb")
            return auth.access_denied_reply()
        #
        id_data = jwt.decode(oidc_token["id_token"], options={"verify_signature": False})
        #
        log.info("ID data: %s", id_data)
        #
        if "sub" not in id_data:
            log.error("Invalid ID token: no sub")
            return auth.access_denied_reply()
        #
        oidc_sub = id_data["sub"]
        #
        auth_ok = True
        # log.info("Auth: %s", auth_ok)
        #
        if "preferred_username" not in id_data:
            auth_name = oidc_sub
        else:
            auth_name = id_data["preferred_username"]
        # log.info("User: %s", auth_name)
        #
        auth_attributes = id_data
        #
        # log.info("Auth attributes: %s", auth_attributes)
        #
        auth_sessionindex = oidc_token["id_token"]
        #
        if "exp" not in id_data:
            auth_exp = datetime.datetime.now() + datetime.timedelta(seconds=86400)  # 24h
        else:
            auth_exp = datetime.datetime.fromtimestamp(id_data["exp"])
        #
        try:
            auth_user_id = \
                self.context.rpc_manager.call.auth_get_user_from_provider(
                    auth_name
                )["id"]
        except:
            auth_user_id = None
        #
        d = {'done': True,
             'error': '',
             'expiration': datetime.datetime(2023, 1, 31, 11, 43, 39),
             'provider': 'oidc',
             'provider_attr': {
                 'nameid': 'admin',
                 'attributes': {
                     'jti': '46a62227-849d-4bb7-86a9-fe4f402371f6', 'exp': 1675165419,
                     'nbf': 0, 'iat': 1675154619,
                     'iss': 'http://192.168.100.13/auth/realms/carrier', 'aud': 'carrier-oidc',
                     'sub': '31f6b97d-4e42-4816-b4bc-4e5b1bc3b181', 'typ': 'ID',
                     'azp': 'carrier-oidc', 'auth_time': 1675154619,
                     'session_state': '73a629cd-6b2d-4049-aef0-16f0252f3e41', 'acr': '1',
                     'email_verified': True, 'groups': ['/BSS', '/Carrier', '/EPAM'],
                     'preferred_username': 'admin'},
                 'sessionindex': 'eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJyTVBZMW1hT1hCX3FZbERWNXVaa0xlZjd4MzZnRktzUmVIVUNUZ2VZTG5jIn0.eyJqdGkiOiI0NmE2MjIyNy04NDlkLTRiYjctODZhOS1mZTRmNDAyMzcxZjYiLCJleHAiOjE2NzUxNjU0MTksIm5iZiI6MCwiaWF0IjoxNjc1MTU0NjE5LCJpc3MiOiJodHRwOi8vMTkyLjE2OC4xMDAuMTMvYXV0aC9yZWFsbXMvY2FycmllciIsImF1ZCI6ImNhcnJpZXItb2lkYyIsInN1YiI6IjMxZjZiOTdkLTRlNDItNDgxNi1iNGJjLTRlNWIxYmMzYjE4MSIsInR5cCI6IklEIiwiYXpwIjoiY2Fycmllci1vaWRjIiwiYXV0aF90aW1lIjoxNjc1MTU0NjE5LCJzZXNzaW9uX3N0YXRlIjoiNzNhNjI5Y2QtNmIyZC00MDQ5LWFlZjAtMTZmMDI1MmYzZTQxIiwiYWNyIjoiMSIsImVtYWlsX3ZlcmlmaWVkIjp0cnVlLCJncm91cHMiOlsiL0JTUyIsIi9DYXJyaWVyIiwiL0VQQU0iXSwicHJlZmVycmVkX3VzZXJuYW1lIjoiYWRtaW4ifQ.WNvZbvqnLOyLsHNwTdd6QwPus_1k342cOQyTg9I0Cs8eddF7fP_-RmnsJ_icjN_v379mih5HvryxMWVhkL7YNItGg_y3Y-E7e26XxifCOmHIorZvEI3fY57fzSIDommzWni8OWuxJSoH9lmTXsFTnMgtLXJV4FJ9XLXLgnh1edc2pA2sC8yl6QIgG8aQUO834LTBNT2c6xkYZrYU41tsT31SdZ_5ooknOnen4UaR17QC3JGS5TQmWtgWgbKfgel0UG_bWpNByqeQMeFLyVl34a1ZapC4BBS6sGusAGwMSX6ZJaYvfsHkmk1DLWURpzOOIAOKLiHmPGenH_qz8EIpuA'},
             'user_id': None
             }
        auth_ctx = auth.get_auth_context()
        auth_ctx["done"] = auth_ok
        auth_ctx["error"] = ""
        auth_ctx["expiration"] = auth_exp
        auth_ctx["provider"] = "oidc"
        auth_ctx["provider_attr"]["nameid"] = auth_name
        auth_ctx["provider_attr"]["attributes"] = auth_attributes
        auth_ctx["provider_attr"]["sessionindex"] = auth_sessionindex
        auth_ctx["user_id"] = auth_user_id
        auth.set_auth_context(auth_ctx)
        #
        log.info("Context: %s", auth_ctx)
        #
        return auth.access_success_redirect(target_token)

    @web.route("/logout")
    def logout(self):
        """ Logout """
        target_token = flask.request.args.get("target_to", "")
        auth_ctx = auth.get_auth_context()
        #
        if "auth_oidc" not in flask.session:
            flask.session["auth_oidc"] = dict()
        #
        while True:
            state_uuid, target_state = tools.generate_state_id(self)
            if state_uuid not in flask.session["auth_oidc"]:
                break
        #
        flask.session["auth_oidc"][state_uuid] = dict()
        flask.session["auth_oidc"][state_uuid]["target_token"] = target_token
        flask.session.modified = True
        #
        url_params = urllib.parse.urlencode({
            "id_token_hint": auth_ctx["provider_attr"].get("sessionindex", ""),
            "post_logout_redirect_uri": flask.url_for("auth_oidc.logout_callback"),
            "state": target_state,
        })
        return flask.redirect(f'{self.descriptor.config["end_session_endpoint"]}?{url_params}')
        #
        # return self.descriptor.render_template(
        #     "redirect.html",
        #     action=self.descriptor.config["end_session_endpoint"],
        #     parameters=[
        #         {
        #             "name": "id_token_hint",
        #             "value": auth_ctx["provider_attr"].get("sessionindex", ""),
        #         },
        #         {
        #             "name": "post_logout_redirect_uri",
        #             "value": flask.url_for("auth_oidc.logout_callback"),
        #         },
        #         {
        #             "name": "state",
        #             "value": target_state,
        #         },
        #     ],
        # )

    @web.route("/logout_callback")
    def logout_callback(self):  # pylint: disable=R0912,R0914,R0915
        """ Logout callback """
        log.info("GET arguments: %s", flask.request.args)
        #
        if "state" not in flask.request.args:
            log.error("No state in OIDC callback")
            return auth.access_denied_reply()
        #
        target_state = flask.request.args["state"]
        #
        try:
            state_uuid = tools.get_state_id(self, target_state)
            if state_uuid not in flask.session["auth_oidc"]:
                raise ValueError("Unknown state")
        except:  # pylint: disable=W0702
            log.error("Invalid state")
            return auth.access_denied_reply()
        #
        oidc_state = flask.session["auth_oidc"].pop(state_uuid)
        flask.session.modified = True
        #
        target_token = oidc_state.get("target_token", "")
        #
        return auth.logout_success_redirect(target_token)
