package com.meetingoneline.meeting_one_line.user;
import com.meetingoneline.meeting_one_line.auth.refresh_token.RefreshTokenEntity;
import com.meetingoneline.meeting_one_line.global.entity.SoftDeletableEntity;
import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.SQLRestriction;

import java.util.ArrayList;
import java.util.List;

@Getter
@Entity
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@SQLRestriction("deleted_at IS NULL")
@Table(name = "users")
public class UserEntity extends SoftDeletableEntity {
    @Column(nullable = false, unique = true, length = 100)
    private String email;

    @Column(nullable = false, length = 255)
    private String password;

    @Column(nullable = false, unique = true, length = 20)
    private String nickname;

    // 연관관계
    @OneToMany(mappedBy = "user", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<MeetingEntity> meetings = new ArrayList<>();

    @OneToMany(mappedBy = "user", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<RefreshTokenEntity> refreshTokens = new ArrayList<>();

    public static UserEntity create(String email, String password, String nickname){
        UserEntity user = new UserEntity();
        user.email = email;
        user.password = password;
        user.nickname=  nickname;

        return user;
    }
}
