CREATE DATABASE hzm_bus_demo;
use hzm_bus_demo;

CREATE TABLE hzmbus_v_ticket_wait (
    id int(11) NOT NULL PRIMARY KEY AUTO_INCREMENT,
    username char(50) NOT NULL,
    idcard char(50) NOT NULL,
    buy_date char(20) NOT NULL DEFAULT 'any'
);

CREATE TABLE hzmbus_t_ticket (
    id int(11) NOT NULL PRIMARY KEY AUTO_INCREMENT,
    is_run int(11) NOT NULL DEFAULT '0'
);

CREATE TABLE hzmbus_t_log (
    id int(11) NOT NULL PRIMARY KEY AUTO_INCREMENT,
    account_username char(50) NOT NULL,
    account_password char(50) NOT NULL,
    log_level CHAR(20) NOT NULL,
    log_info VARCHAR(255) NOT NULL,
    tickets VARCHAR(255) NOT NULL,
    ident CHAR(50) NOT NULL
);

CREATE TABLE hzmbus_t_buy_account (
    id int(11) NOT NULL PRIMARY KEY AUTO_INCREMENT,
    username char(50) NOT NULL,
    password char(50) NOT NULL,
    accountlock int(11) NOT NULL DEFAULT '0'
);