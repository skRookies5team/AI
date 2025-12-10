package com.meetingoneline.meeting_one_line;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;

@SpringBootApplication
@EnableJpaAuditing
public class MeetingOneLineApplication {

	public static void main(String[] args) {
		SpringApplication.run(MeetingOneLineApplication.class, args);
	}

}
