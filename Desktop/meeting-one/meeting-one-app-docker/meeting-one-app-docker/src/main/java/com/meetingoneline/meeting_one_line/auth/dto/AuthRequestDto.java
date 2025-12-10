package com.meetingoneline.meeting_one_line.auth.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;

public class AuthRequestDto {

    /**
     * 회원가입 API 요청
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(name = "AuthRequestSignupDto", description = "소셜 회원가입 요청 DTO")
    public static class SignupRequest{
        @NotBlank(message = "이메일은 필수입니다.")
        @Email
        @Schema(
            description = "사용자 이메일 주소",
            example = "test1@test.com"
        )
        private String email;

        @NotBlank(message = "닉네임은 필수입니다.")
        @Size(min = 2, max = 12, message = "닉네임은 2자 이상 20자 이하여야 합니다.")
        @Schema(
                description = "닉네임 (2~12자)",
                example = "test1",
                minLength = 2,
                maxLength = 12
        )
        private String nickname;

        @NotBlank(message = "비밀번호는 필수입니다.")
        @Schema(
                description =  "비밀번호 (8~20자), 영문/숫자 조합 권장",
                example = "test1234",
                format = "password",
                minLength = 8,
                maxLength = 20
        )
        @Size(min = 8, max = 20, message = "비밀번호는 8자 이상 20자 이하이어야 합니다.")
        private String password;
    }

    /**
     * 로그인 API 요청
     */
    @Getter
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(name = "AuthRequestEmailLoginDto", description = "로그인 요청 DTO")
    public static class EmailLogin{
        @NotBlank
        @Email
        @Schema(description = "이메일", example = "test1@test.com")
        private String email;

        @NotBlank
        @Schema(description = "비밀번호", example = "test1234", minLength = 8,
                maxLength = 20)
        @Size(min = 8, max = 20, message = "비밀번호는 8자 이상 20자 이하이어야 합니다.")
        private String password;
    }

    /* 재석님 */
}
