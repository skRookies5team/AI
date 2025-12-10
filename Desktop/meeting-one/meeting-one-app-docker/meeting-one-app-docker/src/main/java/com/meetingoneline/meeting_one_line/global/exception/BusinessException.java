package com.meetingoneline.meeting_one_line.global.exception;

import lombok.Getter;

@Getter
public class BusinessException extends RuntimeException {

    private final ErrorCode code;

    public BusinessException(ErrorCode code) {
        super(code.getMessage());
        this.code = code;
    }

    public BusinessException(ErrorCode code, String customMessage) {
        super(customMessage);
        this.code = code;
    }
}
